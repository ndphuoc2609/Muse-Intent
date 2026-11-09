"""A completed reply is delivered even when validation flags it or goal is handoff."""
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import SETTINGS
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.engine.validator import validate
from app.models import Delivery, Message, PersonaFact, PersonaStory, QuestionState, StoryTold
from app.providers.fake import FakeProvider
from conftest import make_persona, turn
from test_color_propose_flow import ColorProposingProvider


def test_handoff_goal_still_reports_unconfirmed_transaction():
    errors = validate({"goal": "handoff", "text": "Mình đã đặt cọc cho bạn rồi nhé.",
                       "claims": []}, {"retrieved": {}})
    assert any("transaction_confirmation" in error for error in errors)


class ReplyWithValidationError:
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


class HandoffProvider:
    def __init__(self):
        self.provider = ColorProposingProvider()

    def chat(self, messages, tools):
        result = self.provider.chat(messages, tools)
        for call in result.tool_calls:
            if call.name == "submit_final_reply":
                call.arguments["goal"] = "handoff"
                call.arguments["text"] += " Bạn thích màu nào?"
                call.arguments["followup_question"] = "Bạn thích màu nào?"
                call.arguments["followup_key"] = "color_preference"
        return result


def test_validation_error_is_sent_and_saved_with_its_diagnostics(session, monkeypatch):
    monkeypatch.setattr(SETTINGS, "max_validator_repairs", 0)
    sender = FakeSender()
    pipe = Pipeline(session, ReplyWithValidationError(ColorProposingProvider(), followup=True), sender)
    result = turn(pipe, "Bác đi xe màu gì thế?", idem="auto-validation")

    assert result.status == "sent"
    assert len(sender.sent) == 1 and sender.sent[0]["message_id"] == result.message_id
    assert any("uncovered_factual_assertion" in e for e in result.validator_errors)
    msg = session.get(Message, result.message_id)
    assert msg.status == "sent"
    assert any("uncovered_factual_assertion" in e for e in msg.trace["validator_errors"])
    assert session.query(PersonaFact).filter_by(persona_id="AUTO_029", fact_key="vehicle_color",
                                                status="active").count() == 1
    assert session.query(QuestionState).filter_by(thread_id=result.thread_id,
        question_key="color_preference", status="pending", asked_message_id=result.message_id).count() == 1
    assert session.query(Delivery).filter_by(message_id=result.message_id, status="sent").count() == 1


def test_handoff_goal_is_sent_and_records_delivered_followup(session, monkeypatch):
    monkeypatch.setattr(SETTINGS, "max_validator_repairs", 0)
    sender = FakeSender()
    result = turn(Pipeline(session, HandoffProvider(), sender), "Bác đi xe màu gì thế?")

    assert result.status == "sent"
    assert len(sender.sent) == 1
    assert session.query(PersonaFact).filter_by(persona_id="AUTO_029", fact_key="vehicle_color",
                                                status="active").count() == 1
    assert session.query(QuestionState).filter_by(thread_id=result.thread_id,
        question_key="color_preference", status="pending", asked_message_id=result.message_id).count() == 1
    assert session.get(Message, result.message_id).status == "sent"


def test_story_is_activated_and_marked_told_even_with_validation_error(session, monkeypatch):
    monkeypatch.setattr(SETTINGS, "max_validator_repairs", 0)
    pid = make_persona(session, "auto-review-story", {"vehicle_model": "Hyundai Creta"})
    sender = FakeSender()
    result = turn(Pipeline(session, ReplyWithValidationError(FakeProvider()), sender),
                  "Bác có kỷ niệm nào đáng nhớ với xe không?", persona_id=pid)

    assert result.status == "sent" and result.saved_stories
    event_id = result.saved_stories[0]["event_id"]
    story = session.get(PersonaStory, event_id)
    assert story.status == "active" and story.told_in_message_id == result.message_id
    assert session.query(StoryTold).filter_by(event_id=event_id, message_id=result.message_id).count() == 1
    assert len(sender.sent) == 1


def test_failed_auto_send_retries_same_draft_and_restores_fact(session, monkeypatch):
    monkeypatch.setattr(SETTINGS, "max_validator_repairs", 0)
    text = "Bác đi xe màu gì thế?"
    first = turn(Pipeline(session, ReplyWithValidationError(ColorProposingProvider()), FakeSender(True)),
                 text, idem="auto-retry")
    assert first.status == "error"
    fact = session.execute(select(PersonaFact).where(
        PersonaFact.persona_id == "AUTO_029", PersonaFact.fact_key == "vehicle_color")).scalar_one()
    fact_id = fact.id
    assert fact.status == "rejected"
    assert any("uncovered_factual_assertion" in e for e in session.get(Message, first.message_id).trace["validator_errors"])

    with sessionmaker(bind=session.get_bind())() as fresh:
        sender = FakeSender()
        pipe = Pipeline(fresh, FakeProvider(), sender)
        retried = turn(pipe, text, thread_id=first.thread_id, idem="auto-retry")
        assert retried.status == "sent" and retried.message_id == first.message_id
        assert any("uncovered_factual_assertion" in e for e in retried.validator_errors)
        assert fresh.get(PersonaFact, fact_id).status == "active"
        assert any("uncovered_factual_assertion" in e for e in fresh.get(Message, first.message_id).trace["validator_errors"])
        replay = turn(pipe, text, thread_id=first.thread_id, idem="auto-retry")
        assert replay.status == "sent"
        assert any("uncovered_factual_assertion" in e for e in replay.validator_errors)
        assert any("uncovered_factual_assertion" in e for e in replay.trace["validator_errors"])
        assert len(sender.sent) == 1


def test_failed_retry_preserves_validator_errors_and_delivery_failure(session, monkeypatch):
    monkeypatch.setattr(SETTINGS, "max_validator_repairs", 0)
    text = "Bác đi xe màu gì thế?"
    first = turn(Pipeline(session, ReplyWithValidationError(ColorProposingProvider()), FakeSender(True)),
                 text, idem="auto-retry-fails")
    assert first.status == "error"
    with sessionmaker(bind=session.get_bind())() as fresh:
        failed_sender = FakeSender(True)
        retry = turn(Pipeline(fresh, FakeProvider(), failed_sender), text,
                     thread_id=first.thread_id, idem="auto-retry-fails")
        assert retry.status == "error"
        assert retry.message_id == first.message_id
        assert any("uncovered_factual_assertion" in e for e in retry.validator_errors)
        assert (any("fake_sender_failed" in e or "recovery_failed" in e
                    for e in retry.validator_errors)
                or retry.trace.get("recovery_failed") is True)
        assert any("uncovered_factual_assertion" in e for e in retry.trace["validator_errors"])
        assert failed_sender.sent == []
        assert fresh.query(PersonaFact).filter_by(persona_id="AUTO_029",
                fact_key="vehicle_color", status="active").count() == 0
