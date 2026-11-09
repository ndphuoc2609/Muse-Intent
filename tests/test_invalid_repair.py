"""Invalid model repairs must not replace an existing usable reply."""
import pytest

from app.config import SETTINGS
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.models import PersonaFact
from app.providers.base import ProviderResult, ToolCall
from conftest import turn
from test_auto_send_review import ReplyWithValidationError
from test_color_propose_flow import ColorProposingProvider


class InvalidRepairProvider:
    def __init__(self, arguments):
        self.arguments = arguments
        self.initial = ReplyWithValidationError(ColorProposingProvider())

    def chat(self, messages, tools):
        if any(m.get("role") == "user" and
               str(m.get("content", "")).startswith("VALIDATOR_FEEDBACK:")
               for m in messages):
            return ProviderResult(tool_calls=[ToolCall("submit_final_reply", self.arguments)])
        return self.initial.chat(messages, tools)


class EmptyFinalProvider:
    def __init__(self, text):
        self.text = text
        self.initial = ColorProposingProvider()

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
    result = turn(Pipeline(session, InvalidRepairProvider(arguments), sender),
                  "Bác đi xe màu gì thế?", idem="invalid-repair")

    assert result.status == "sent"
    assert result.reply_text == "Xe mình màu trắng đó bạn. Giá xe là 999 triệu đồng."
    assert any("uncovered_factual_assertion" in error for error in result.validator_errors)
    assert len(sender.sent) == 1 and sender.sent[0]["text"] == result.reply_text
    assert session.query(PersonaFact).filter_by(persona_id="AUTO_029",
                                               fact_key="vehicle_color",
                                               status="active").count() == 1


@pytest.mark.parametrize("empty_text", ["", "   "])
def test_empty_initial_reply_is_not_sent(session, empty_text):
    sender = FakeSender()
    result = turn(Pipeline(session, EmptyFinalProvider(empty_text), sender),
                  "Bác đi xe màu gì thế?", idem="empty-final")

    assert result.status == "error"
    assert not result.reply_text
    assert sender.sent == []
    assert session.query(PersonaFact).filter_by(persona_id="AUTO_029",
                                               fact_key="vehicle_color",
                                               status="active").count() == 0
