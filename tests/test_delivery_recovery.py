"""Recovery phải khôi phục dữ kiện cùng với bản nháp đã gửi, kể cả qua session mới."""
import json

import pytest
from sqlalchemy.orm import sessionmaker

from app.engine.delivery_state import can_recover, make_snapshot
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.engine.tools import ToolExecutor
from app.models import (CustomerFact, Delivery, Message, PersonaFact,
                        PersonaStory, QuestionState, StoryTold, Thread)
from app.providers.base import ProviderResult, ToolCall
from app.providers.fake import FakeProvider
from conftest import turn


class NoCallsProvider:
    def chat(self, messages, tools):
        raise AssertionError("retry không được gọi lại model khi đã có draft")


class BuyingInterestProvider:
    """plan -> propose_customer_fact -> final reply (luồng pending fact
    còn sống sau khi propose_persona_fact bị gỡ)."""

    def chat(self, messages, tools):
        called = {json.loads(m["content"]).get("tool") for m in messages
                  if m.get("role") == "tool"}
        if "plan_turn" not in called:
            return ProviderResult(tool_calls=[ToolCall("plan_turn", {
                "intents": [{"type": "share_need",
                             "normalized": "đang tính mua Creta",
                             "evidence": "Em đang tính mua Creta"}],
                "goal": "answer_question"})])
        if "propose_customer_fact" not in called:
            return ProviderResult(tool_calls=[ToolCall("propose_customer_fact", {
                "subject": "self", "predicate": "buying_interest",
                "value_raw": "đang tính mua Creta",
                "evidence": "Em đang tính mua Creta"})])
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": "Dạ, Creta đi phố ổn lắm bạn. Bạn thích điểm gì ở mẫu xe này?",
            "claims": [], "goal": "answer_question",
            "followup_question": "Bạn thích điểm gì ở mẫu xe này?",
            "followup_key": "like_point"})])


def new_session(session):
    return sessionmaker(bind=session.get_bind())()


def test_retry_restores_customer_fact_and_replay_does_not_resend(session):
    first = turn(Pipeline(session, BuyingInterestProvider(), FakeSender(True)),
                 "Em đang tính mua Creta", idem="buying-interest")
    assert first.status == "error"
    row = session.query(CustomerFact).filter_by(
        thread_id=first.thread_id, actor_id="actor-1",
        predicate="buying_interest").one()
    fact_id = row.id
    assert row.status != "current"

    with new_session(session) as retry_session:
        sender = FakeSender()
        pipe = Pipeline(retry_session, NoCallsProvider(), sender)
        recovered = turn(pipe, "Em đang tính mua Creta",
                         thread_id=first.thread_id, idem="buying-interest")
        assert recovered.status == "sent"
        assert recovered.message_id == first.message_id
        assert retry_session.get(CustomerFact, fact_id).status == "current"
        replay = turn(pipe, "Em đang tính mua Creta",
                      thread_id=first.thread_id, idem="buying-interest")
        assert replay.status == "sent" and replay.message_id == first.message_id
        assert len(sender.sent) == 1
        assert retry_session.query(CustomerFact).filter_by(
            thread_id=first.thread_id, actor_id="actor-1",
            predicate="buying_interest", status="current").count() == 1
        assert retry_session.query(Delivery).filter_by(message_id=first.message_id,
                                                       status="sent").count() == 1


def test_recovery_database_failure_does_not_partially_activate_fact(session, monkeypatch):
    import app.engine.pipeline as pipeline_module

    text = "Em đang tính mua Creta"
    first = turn(Pipeline(session, BuyingInterestProvider(), FakeSender(True)),
                 text, idem="atomic")
    fact = session.query(CustomerFact).filter_by(
        thread_id=first.thread_id, actor_id="actor-1",
        predicate="buying_interest").one()
    fact_id = fact.id
    finish = pipeline_module.finish_delivery

    def fail_before_commit(*args, **kwargs):
        finish(*args, **kwargs)
        raise RuntimeError("database commit unavailable")

    monkeypatch.setattr(pipeline_module, "finish_delivery", fail_before_commit)
    with new_session(session) as retry_session:
        with pytest.raises(RuntimeError, match="database commit unavailable"):
            turn(Pipeline(retry_session, NoCallsProvider(), FakeSender()), text,
                 thread_id=first.thread_id, idem="atomic")
        retry_session.rollback()

    with new_session(session) as check:
        assert check.get(CustomerFact, fact_id).status != "current"
        assert check.query(Delivery).filter_by(message_id=first.message_id,
                                               status="sent").count() == 0
        assert check.get(Message, first.message_id).status == "sending"


def test_retry_restores_story_told_once(session):
    text = "Bác có kỷ niệm nào đáng nhớ với xe không?"
    first = turn(Pipeline(session, FakeProvider(), FakeSender(True)), text,
                 idem="story")
    assert first.status == "error"
    assert not session.query(StoryTold).filter_by(
        thread_id=first.thread_id).count()

    with new_session(session) as retry_session:
        sender = FakeSender()
        pipe = Pipeline(retry_session, NoCallsProvider(), sender)
        recovered = turn(pipe, text, thread_id=first.thread_id, idem="story")
        assert recovered.status == "sent" and recovered.message_id == first.message_id
        told = retry_session.query(StoryTold).filter_by(
            thread_id=first.thread_id, actor_id="actor-1",
            message_id=first.message_id).all()
        assert len(told) == 1
        replay = turn(pipe, text, thread_id=first.thread_id, idem="story")
        assert replay.status == "sent" and len(sender.sent) == 1
        assert retry_session.query(StoryTold).filter_by(
            thread_id=first.thread_id, actor_id="actor-1",
            message_id=first.message_id).count() == 1


class CorrectionProvider:
    def chat(self, messages, tools):
        called = {json.loads(m["content"]).get("tool") for m in messages
                  if m.get("role") == "tool"}
        if "plan_turn" not in called:
            return ProviderResult(tool_calls=[ToolCall("plan_turn", {
                "intents": [{"type": "correction", "subject": "self",
                             "normalized": "khách đính chính", "evidence": "À không, mua cho mình"}],
                "goal": "answer_question"})])
        if "propose_customer_fact" not in called:
            return ProviderResult(tool_calls=[ToolCall("propose_customer_fact", {
                "subject": "self", "predicate": "intended_user", "value_raw": "mình",
                "evidence": "À không, mua cho mình", "correction": True})])
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": "Mình hiểu rồi. Bạn thích điểm gì ở mẫu xe này?", "claims": [],
            "goal": "answer_question", "followup_question": "Bạn thích điểm gì ở mẫu xe này?",
            "followup_key": "like_point"})])


def test_retry_restores_customer_correction_and_followup(session):
    thread = Thread(persona_id="AUTO_029", post_id="post-1", post_text="Bài về Creta.",
                    post_vehicle_models=["Hyundai Creta"])
    session.add(thread)
    session.commit()
    old = CustomerFact(thread_id=thread.id, actor_id="actor-1", subject="third_person",
                       relation="bố", predicate="intended_user", value_raw="bố",
                       evidence="mua cho bố", source="stated", status="current")
    other = CustomerFact(thread_id=thread.id, actor_id="actor-2", subject="third_person",
                         relation="mẹ", predicate="intended_user", value_raw="mẹ",
                         evidence="mua cho mẹ", source="stated", status="current")
    session.add_all([old, other])
    session.commit()
    old_id, other_id, tid = old.id, other.id, thread.id
    text = "À không, mua cho mình"
    first = turn(Pipeline(session, CorrectionProvider(), FakeSender(True)), text,
                 thread_id=tid, idem="correction")
    assert first.status == "error", first.validator_errors
    new = session.query(CustomerFact).filter_by(thread_id=tid, actor_id="actor-1",
                                                 value_raw="mình").one()
    new_id = new.id
    assert new.status != "current"
    assert session.get(CustomerFact, old_id).status == "current"
    assert session.query(QuestionState).filter_by(thread_id=tid,
                                                   question_key="like_point").count() == 0

    with new_session(session) as retry_session:
        sender = FakeSender()
        pipe = Pipeline(retry_session, NoCallsProvider(), sender)
        recovered = turn(pipe, text, thread_id=tid, idem="correction")
        assert recovered.status == "sent" and recovered.message_id == first.message_id
        assert retry_session.get(CustomerFact, new_id).status == "current"
        assert retry_session.get(CustomerFact, old_id).status == "corrected"
        assert retry_session.get(CustomerFact, other_id).status == "current"
        qs = retry_session.query(QuestionState).filter_by(thread_id=tid, actor_id="actor-1",
                              question_key="like_point", asked_message_id=first.message_id).all()
        assert len(qs) == 1 and qs[0].status == "pending"
        replay = turn(pipe, text, thread_id=tid, idem="correction")
        assert replay.status == "sent" and len(sender.sent) == 1
        assert retry_session.query(QuestionState).filter_by(thread_id=tid, actor_id="actor-1",
                                                            question_key="like_point").count() == 1


def test_multiple_delivery_failures_keep_same_draft_and_then_recover(session):
    text = "Em đang tính mua Creta"
    first = turn(Pipeline(session, BuyingInterestProvider(), FakeSender(True)), text, idem="retry")
    assert first.status == "error"
    with new_session(session) as retry_session:
        failed = turn(Pipeline(retry_session, NoCallsProvider(), FakeSender(True)), text,
                      thread_id=first.thread_id, idem="retry")
        assert failed.status == "error" and failed.message_id == first.message_id
        assert retry_session.query(CustomerFact).filter_by(
            thread_id=first.thread_id, actor_id="actor-1",
            predicate="buying_interest", status="current").count() == 0
        sender = FakeSender()
        recovered = turn(Pipeline(retry_session, NoCallsProvider(), sender), text,
                         thread_id=first.thread_id, idem="retry")
        assert recovered.status == "sent" and recovered.message_id == first.message_id
        assert recovered.reply_text == first.reply_text and len(sender.sent) == 1
        assert retry_session.query(CustomerFact).filter_by(
            thread_id=first.thread_id, actor_id="actor-1",
            predicate="buying_interest", status="current").count() == 1
        assert retry_session.query(Message).filter_by(thread_id=first.thread_id, role="user").count() == 1


def test_provider_error_never_sends_empty_draft_on_retry(session):
    class BrokenProvider:
        def chat(self, messages, tools):
            raise RuntimeError("provider unavailable")

    text = "Bác đang đi xe gì?"
    first = turn(Pipeline(session, BrokenProvider(), FakeSender()), text, idem="no-final")
    assert first.status == "error" and not first.reply_text
    with new_session(session) as retry_session:
        sender = FakeSender()
        retried = turn(Pipeline(retry_session, BrokenProvider(), sender), text,
                       thread_id=first.thread_id, idem="no-final")
        assert retried.status == "error"
        assert sender.sent == []


def test_faq_failed_delivery_can_retry_and_replay(session):
    text = "Cảm ơn bác"
    first = turn(Pipeline(session, NoCallsProvider(), FakeSender(True)), text, idem="faq")
    assert first.status == "error" and first.reply_text
    with new_session(session) as retry_session:
        sender = FakeSender()
        pipe = Pipeline(retry_session, NoCallsProvider(), sender)
        recovered = turn(pipe, text, thread_id=first.thread_id, idem="faq")
        assert recovered.status == "sent" and recovered.message_id == first.message_id
        assert recovered.reply_text == first.reply_text
        assert turn(pipe, text, thread_id=first.thread_id, idem="faq").status == "sent"
        assert len(sender.sent) == 1


def test_retry_marks_existing_story_told_only_once_for_duplicate_claim(session):
    from test_storytold_dedupe import _DupClaimProvider

    text = "Trời mưa đường ngập thì sao?"
    first = turn(Pipeline(session, _DupClaimProvider(), FakeSender(True)), text,
                 idem="retold")
    assert first.status == "error"
    assert session.query(StoryTold).filter_by(event_id="AUTO_029_mem_3",
                                              message_id=first.message_id).count() == 0
    with new_session(session) as retry_session:
        sender = FakeSender()
        pipe = Pipeline(retry_session, NoCallsProvider(), sender)
        recovered = turn(pipe, text, thread_id=first.thread_id, idem="retold")
        assert recovered.status == "sent"
        assert retry_session.query(StoryTold).filter_by(event_id="AUTO_029_mem_3",
                    actor_id="actor-1", message_id=first.message_id).count() == 1
        assert turn(pipe, text, thread_id=first.thread_id, idem="retold").status == "sent"
        assert len(sender.sent) == 1
        assert retry_session.query(StoryTold).filter_by(event_id="AUTO_029_mem_3",
                    actor_id="actor-1", message_id=first.message_id).count() == 1


def test_stale_persona_fact_blocks_recovery(session):
    # Canon đổi sau khi snapshot được tạo -> can_recover False: không
    # được gửi lại bản nháp dựa trên fact cũ. (In-turn propose đã gỡ
    # nên snapshot được dựng trực tiếp từ một pending fact.)
    thread = Thread(persona_id="AUTO_029", post_id="post-1",
                    post_text="Bài về Creta.",
                    post_vehicle_models=["Hyundai Creta"])
    session.add(thread)
    session.commit()
    pending = PersonaFact(persona_id="AUTO_029", fact_key="vehicle_color",
                          value="trắng", source="test", status="pending")
    session.add(pending)
    session.commit()
    msg = Message(thread_id=thread.id, role="persona", actor_id="actor-1",
                  text="Xe mình màu trắng đó bạn.", status="error")
    session.add(msg)
    session.commit()
    ex = ToolExecutor(session, "AUTO_029", thread.id, "actor-1")
    ex.pending_persona_fact_ids = [pending.id]
    snapshot = make_snapshot(session, ex, {"claims": [], "goal": "answer_question"},
                             opted_out=False)
    assert can_recover(session, snapshot, msg, "AUTO_029", "actor-1") is True
    session.add(PersonaFact(persona_id="AUTO_029", fact_key="vehicle_color",
                            value="đen", source="test", status="active"))
    session.commit()
    assert can_recover(session, snapshot, msg, "AUTO_029", "actor-1") is False


def test_retry_does_not_send_revoked_story(session):
    from test_storytold_dedupe import _DupClaimProvider

    text = "Trời mưa đường ngập thì sao?"
    first = turn(Pipeline(session, _DupClaimProvider(), FakeSender(True)), text,
                 idem="revoked-story")
    assert first.status == "error"
    story = session.get(PersonaStory, "AUTO_029_mem_3")
    story.status = "revoked"
    session.commit()
    with new_session(session) as retry_session:
        sender = FakeSender()
        retried = turn(Pipeline(retry_session, NoCallsProvider(), sender), text,
                       thread_id=first.thread_id, idem="revoked-story")
        assert retried.status in ("error", "review")
        assert sender.sent == []
        assert retry_session.get(PersonaStory, "AUTO_029_mem_3").status == "revoked"
