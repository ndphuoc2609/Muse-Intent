"""Blocking validation errors hold the reply in review; soft errors ride
along on a sent reply. Handoff with clean validation is sent.

Thay thế hành vi auto-send cũ (PR #1: mọi lỗi đều gửi kèm): lỗi
blocking (sai nguồn/sai xe, dữ liệu cá nhân, opt-out, cam kết giao
dịch) thì KHÔNG gửi — giữ ở review kèm validator_errors; lỗi mềm
(giọng điệu/câu hỏi chung) thì sửa 1 vòng rồi gửi kèm lỗi.
"""
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import SETTINGS
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.engine.validator import validate
from app.models import CustomerFact, Delivery, Message, QuestionState, StoryTold
from app.providers.fake import FakeProvider
from conftest import turn


def test_handoff_goal_still_reports_unconfirmed_transaction():
    errors = validate({"goal": "handoff", "text": "Mình đã đặt cọc cho bạn rồi nhé.",
                       "claims": []}, {"retrieved": {}})
    assert any("transaction_confirmation" in error for error in errors)


class ReplyWithValidationError:
    """Chèn khẳng định tiền không claim vào final -> uncovered (blocking)."""

    def __init__(self, provider, goal=None, followup=False):
        self.provider = provider
        self.goal = goal
        self.followup = followup

    def chat(self, messages, tools):
        result = self.provider.chat(messages, tools)
        for call in result.tool_calls:
            if call.name != "submit_final_reply":
                continue
            call.arguments["text"] += " Giá xe là 999 triệu đồng."
            if self.goal:
                call.arguments["goal"] = self.goal
            if self.followup:
                call.arguments["text"] += " Bạn thích màu nào?"
                call.arguments["followup_question"] = "Bạn thích màu nào?"
                call.arguments["followup_key"] = "color_preference"
        return result


class ReplyWithSoftError:
    """Chèn câu giọng hệ thống vào final -> data_disclaimer (soft)."""

    def __init__(self, provider):
        self.provider = provider

    def chat(self, messages, tools):
        result = self.provider.chat(messages, tools)
        for call in result.tool_calls:
            if call.name == "submit_final_reply":
                call.arguments["text"] += " Mình chưa có dữ liệu để so sánh."
        return result


class HandoffProvider:
    def __init__(self):
        self.provider = FakeProvider()

    def chat(self, messages, tools):
        result = self.provider.chat(messages, tools)
        for call in result.tool_calls:
            if call.name == "submit_final_reply":
                call.arguments["goal"] = "handoff"
                call.arguments["text"] += " Bạn thích màu nào?"
                call.arguments["followup_question"] = "Bạn thích màu nào?"
                call.arguments["followup_key"] = "color_preference"
        return result


def test_blocking_validation_error_is_held_in_review(session, monkeypatch):
    monkeypatch.setattr(SETTINGS, "max_validator_repairs", 0)
    sender = FakeSender()
    pipe = Pipeline(session, ReplyWithValidationError(FakeProvider(), followup=True), sender)
    result = turn(pipe, "Bác đi xe màu gì thế?", idem="auto-validation")

    # Lỗi blocking -> giữ ở review, KHÔNG gửi, không kích hoạt gì.
    assert result.status == "review"
    assert sender.sent == []
    assert any("uncovered_factual_assertion" in e for e in result.validator_errors)
    msg = session.get(Message, result.message_id)
    assert msg.status == "review"
    assert any("uncovered_factual_assertion" in e for e in msg.trace["validator_errors"])
    assert session.query(QuestionState).filter_by(
        thread_id=result.thread_id, question_key="color_preference",
        status="pending").count() == 0
    assert session.query(Delivery).filter_by(
        message_id=result.message_id, status="sent").count() == 0


def test_soft_validation_error_is_sent_with_its_diagnostics(session, monkeypatch):
    monkeypatch.setattr(SETTINGS, "max_validator_repairs", 0)
    sender = FakeSender()
    pipe = Pipeline(session, ReplyWithSoftError(FakeProvider()), sender)
    result = turn(pipe, "Bác đi xe màu gì thế?", idem="auto-soft")

    assert result.status == "sent"
    assert len(sender.sent) == 1 and sender.sent[0]["message_id"] == result.message_id
    assert any("data_disclaimer" in e for e in result.validator_errors)
    msg = session.get(Message, result.message_id)
    assert msg.status == "sent"
    assert any("data_disclaimer" in e for e in msg.trace["validator_errors"])
    assert session.query(Delivery).filter_by(message_id=result.message_id, status="sent").count() == 1


def test_handoff_goal_is_sent_and_records_delivered_followup(session, monkeypatch):
    monkeypatch.setattr(SETTINGS, "max_validator_repairs", 0)
    sender = FakeSender()
    result = turn(Pipeline(session, HandoffProvider(), sender), "Bác đi xe màu gì thế?")

    assert result.status == "sent"
    assert len(sender.sent) == 1
    assert session.query(QuestionState).filter_by(thread_id=result.thread_id,
        question_key="color_preference", status="pending", asked_message_id=result.message_id).count() == 1
    assert session.get(Message, result.message_id).status == "sent"


def test_story_is_marked_told_on_sent_even_with_soft_error(session, monkeypatch):
    monkeypatch.setattr(SETTINGS, "max_validator_repairs", 0)
    sender = FakeSender()
    result = turn(Pipeline(session, ReplyWithSoftError(FakeProvider()), sender),
                  "Bác có kỷ niệm nào đáng nhớ với xe không?")

    assert result.status == "sent"
    assert any("data_disclaimer" in e for e in result.validator_errors)
    # Story từ canon được claim trong final -> đánh dấu đã kể.
    told = session.query(StoryTold).filter_by(message_id=result.message_id).all()
    assert len(told) == 1
    story_id = told[0].event_id
    from app.models import PersonaStory
    story = session.get(PersonaStory, story_id)
    assert story.told_in_message_id == result.message_id
    assert len(sender.sent) == 1


def test_failed_send_retries_same_draft_and_restores_customer_fact(session, monkeypatch):
    monkeypatch.setattr(SETTINGS, "max_validator_repairs", 0)
    text = "Em muốn mua Creta, bác tư vấn giúp"
    first = turn(Pipeline(session, FakeProvider(), FakeSender(True)),
                 text, idem="auto-retry")
    assert first.status == "error"
    fact = session.execute(select(CustomerFact).where(
        CustomerFact.thread_id == first.thread_id,
        CustomerFact.predicate == "buying_interest")).scalar_one()
    fact_id = fact.id
    assert fact.status == "rejected"

    with sessionmaker(bind=session.get_bind())() as fresh:
        sender = FakeSender()
        pipe = Pipeline(fresh, FakeProvider(), sender)
        retried = turn(pipe, text, thread_id=first.thread_id, idem="auto-retry")
        assert retried.status == "sent" and retried.message_id == first.message_id
        assert fresh.get(CustomerFact, fact_id).status == "current"
        replay = turn(pipe, text, thread_id=first.thread_id, idem="auto-retry")
        assert replay.status == "sent"
        assert len(sender.sent) == 1


def test_failed_retry_preserves_delivery_failure(session, monkeypatch):
    monkeypatch.setattr(SETTINGS, "max_validator_repairs", 0)
    text = "Em đang tính mua Creta, bác tư vấn giúp"
    first = turn(Pipeline(session, FakeProvider(), FakeSender(True)),
                 text, idem="auto-retry-fails")
    assert first.status == "error"
    with sessionmaker(bind=session.get_bind())() as fresh:
        failed_sender = FakeSender(True)
        retry = turn(Pipeline(fresh, FakeProvider(), failed_sender), text,
                     thread_id=first.thread_id, idem="auto-retry-fails")
        assert retry.status == "error"
        assert retry.message_id == first.message_id
        assert (any("fake_sender_failed" in e or "recovery_failed" in e
                    for e in retry.validator_errors)
                or retry.trace.get("recovery_failed") is True)
        assert failed_sender.sent == []
        assert fresh.query(CustomerFact).filter_by(
            thread_id=first.thread_id, predicate="buying_interest",
            status="current").count() == 0
