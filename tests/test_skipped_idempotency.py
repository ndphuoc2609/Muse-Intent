"""A skipped comment remains skipped when the same request is retried."""
from sqlalchemy import select

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.models import Message, QuestionState
from conftest import turn


class NoCallsProvider:
    def chat(self, messages, tools):
        raise AssertionError("a skipped replay must not call the provider")


def test_skipped_replay_does_not_rerun_gate_after_question_is_added(session):
    sender = FakeSender()
    pipe = Pipeline(session, NoCallsProvider(), sender)
    first = turn(pipe, "ok", idem="skip-key")
    assert first.status == "skipped"
    session.add(QuestionState(thread_id=first.thread_id, actor_id="actor-1",
                              question_key="color", question_text="Bạn thích màu nào?",
                              status="pending"))
    session.commit()

    replay = turn(pipe, "ok", thread_id=first.thread_id, idem="skip-key")

    assert replay.status == "skipped"
    assert replay.message_id is None
    assert replay.reply_text is None
    assert sender.sent == []
    assert len(session.execute(select(Message).where(
        Message.thread_id == first.thread_id)).scalars().all()) == 1
