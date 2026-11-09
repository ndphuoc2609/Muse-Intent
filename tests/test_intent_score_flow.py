"""The advisory score follows the original decision through the API lifecycle."""
import pytest
from sqlalchemy import select

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.models import Message, QuestionState
from app.providers.fake import FakeProvider
from conftest import turn


@pytest.mark.parametrize(("text", "action", "status", "score"), [
    ("ok", "bypass", "skipped", 5),
    ("Cảm ơn", "faq", "sent", 50),
    ("Bác đang đi xe gì?", "pipeline", "sent", 70),
    ("Em đang tính mua Creta", "pipeline", "sent", 75),
    ("👍", "bypass", "skipped", 0),
])
def test_score_and_gate_are_visible_and_saved(session, text, action, status, score):
    result = turn(Pipeline(session, FakeProvider(), FakeSender()), text, idem="score")
    assert result.status == status
    assert result.trace["gate"]["action"] == action
    detail = result.trace["intent_score"]
    assert detail["score"] == score and detail["method"] == "rules_v1"
    assert detail["factors"]
    message = session.execute(select(Message).where(
        Message.thread_id == result.thread_id, Message.role == "user"
    )).scalar_one()
    assert message.trace["intent_score"] == detail


def test_replay_keeps_original_score_when_context_changes(session):
    pipe = Pipeline(session, FakeProvider(), FakeSender())
    first = turn(pipe, "ok", idem="same-comment")
    assert first.trace["intent_score"]["score"] == 5
    session.add(QuestionState(thread_id=first.thread_id, actor_id="actor-1",
                              question_key="need", question_text="Bạn muốn gì?",
                              status="pending"))
    session.commit()
    replay = turn(pipe, "ok", idem="same-comment", thread_id=first.thread_id)
    assert replay.status == "skipped"
    assert replay.trace["intent_score"] == first.trace["intent_score"]


def test_pending_question_increases_score_without_changing_gate(session):
    # "ok" chỉ vào pipeline khi đáp án cho câu hỏi có/không đang chờ
    # (Phase 2a: ack trước câu hỏi mở thì gate bỏ qua) — điểm 70 vẫn
    # giữ nguyên vì câu hỏi đang chờ làm tăng độ ưu tiên.
    pipe = Pipeline(session, FakeProvider(), FakeSender())
    first = turn(pipe, "Bác đang đi xe gì?")
    session.add(QuestionState(thread_id=first.thread_id, actor_id="actor-1",
                              question_key="need", question_text="Bạn có muốn mua không?",
                              status="pending"))
    session.commit()
    result = turn(pipe, "ok", thread_id=first.thread_id)
    assert result.trace["gate"]["action"] == "pipeline"
    assert result.trace["intent_score"]["score"] == 70


def test_old_skipped_replay_does_not_invent_a_score(session):
    pipe = Pipeline(session, FakeProvider(), FakeSender())
    first = turn(pipe, "ok", idem="old-score")
    message = session.execute(select(Message).where(
        Message.thread_id == first.thread_id, Message.role == "user"
    )).scalar_one()
    message.trace = {"gate": message.trace["gate"]}
    session.commit()
    replay = turn(pipe, "ok", thread_id=first.thread_id, idem="old-score")
    assert replay.trace.get("intent_score") is None
