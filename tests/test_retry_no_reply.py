from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.providers.fake import FakeProvider
from conftest import turn


class FailedProvider:
    def chat(self, messages, tools):
        raise RuntimeError("provider unavailable")


def test_retry_does_not_send_when_no_reply_was_created(session):
    sender = FakeSender()
    pipe = Pipeline(session, FailedProvider(), sender)
    first = turn(pipe, "Bác đang đi xe gì?", idem="no-reply")
    assert first.status == "error"

    retry = turn(pipe, "Bác đang đi xe gì?", idem="no-reply",
                 thread_id=first.thread_id)
    assert retry.status == "error"
    assert sender.sent == []


def test_retry_sender_exception_does_not_allow_another_send(session):
    text = "Bác đang đi xe gì?"
    first = turn(Pipeline(session, FakeProvider(), FakeSender(True)), text,
                 idem="uncertain")
    assert first.status == "error"

    class UncertainSender:
        def send(self, message_id, text):
            raise RuntimeError("delivery outcome unknown")

    result = turn(Pipeline(session, FailedProvider(), UncertainSender()), text,
                  thread_id=first.thread_id, idem="uncertain")
    assert result.status == "error"
    sender = FakeSender()
    again = turn(Pipeline(session, FailedProvider(), sender), text,
                 thread_id=first.thread_id, idem="uncertain")
    assert again.status == "error"
    assert sender.sent == []
