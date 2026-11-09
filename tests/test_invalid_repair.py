"""Invalid model repairs must not replace an existing usable reply.

Lỗi soft (data_disclaimer) -> một lần repair; repair không hợp lệ
(text None/thiếu/rỗng) thì giữ nguyên bản gốc và gửi kèm diagnostics.
Lỗi blocking thì giữ ở review (xem test_auto_send_review)."""
import pytest

from app.config import SETTINGS
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.providers.base import ProviderResult, ToolCall
from app.providers.fake import FakeProvider
from conftest import turn
from test_auto_send_review import ReplyWithSoftError


class InvalidRepairProvider:
    """Lượt đầu: final có lỗi soft; lượt repair: trả final không hợp lệ."""

    def __init__(self, arguments):
        self.arguments = arguments
        self.initial = ReplyWithSoftError(FakeProvider())
        self.initial_text = None

    def chat(self, messages, tools):
        if any(m.get("role") == "user" and
               str(m.get("content", "")).startswith("VALIDATOR_FEEDBACK:")
               for m in messages):
            return ProviderResult(tool_calls=[ToolCall("submit_final_reply", self.arguments)])
        result = self.initial.chat(messages, tools)
        for call in result.tool_calls:
            if call.name == "submit_final_reply":
                self.initial_text = call.arguments.get("text")
        return result


class EmptyFinalProvider:
    def __init__(self, text):
        self.text = text
        self.initial = FakeProvider()

    def chat(self, messages, tools):
        result = self.initial.chat(messages, tools)
        for call in result.tool_calls:
            if call.name == "submit_final_reply":
                call.arguments["text"] = self.text
        return result


@pytest.mark.parametrize("arguments", [
    {"text": None, "claims": [], "goal": "answer_question"},
    {"claims": [], "goal": "answer_question"},
    {"text": "", "claims": [], "goal": "answer_question"},
    {"text": "   ", "claims": [], "goal": "answer_question"},
])
def test_invalid_repair_keeps_and_sends_original_with_diagnostics(session, monkeypatch, arguments):
    monkeypatch.setattr(SETTINGS, "max_validator_repairs", 1)
    sender = FakeSender()
    provider = InvalidRepairProvider(arguments)
    result = turn(Pipeline(session, provider, sender),
                  "Bác đi xe màu gì thế?", idem="invalid-repair")

    assert result.status == "sent"
    assert result.reply_text == provider.initial_text
    assert result.reply_text.endswith("Mình chưa có dữ liệu để so sánh.")
    assert any("data_disclaimer" in error for error in result.validator_errors)
    assert len(sender.sent) == 1 and sender.sent[0]["text"] == result.reply_text


@pytest.mark.parametrize("empty_text", ["", "   "])
def test_empty_initial_reply_is_not_sent(session, empty_text):
    sender = FakeSender()
    result = turn(Pipeline(session, EmptyFinalProvider(empty_text), sender),
                  "Bác đi xe màu gì thế?", idem="empty-final")

    assert result.status == "error"
    assert not result.reply_text
    assert sender.sent == []
