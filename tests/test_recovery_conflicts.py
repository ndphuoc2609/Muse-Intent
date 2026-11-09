"""A stale or unidentifiable draft must never be sent on retry."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import init_db
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.models import Message, PersonaFact, PersonaStory
from app.providers.fake import FakeProvider
from app.seed import seed
from tests.conftest import make_persona, turn


def _failed_turn(session, text, *, persona_id="AUTO_029"):
    sender = FakeSender(fail_next=True)
    pipe = Pipeline(session, FakeProvider(), sender)
    result = turn(pipe, text, idem="retry-key", persona_id=persona_id)
    assert result.status == "error"
    assert sender.sent == []
    return pipe, sender, result


def _retry(pipe, result, text, *, persona_id="AUTO_029"):
    return turn(pipe, text, thread_id=result.thread_id, idem="retry-key",
                persona_id=persona_id)


def test_retry_does_not_send_obsolete_reply_after_new_turn(session):
    text = "Bác đang đi xe gì?"
    pipe, sender, failed = _failed_turn(session, text)
    later = turn(pipe, "Em đang cân nhắc con này cho mẹ.",
                 thread_id=failed.thread_id)
    assert later.status == "sent"
    sent_before = list(sender.sent)

    retried = _retry(pipe, failed, text)

    assert retried.status != "sent"
    assert sender.sent == sent_before


def test_retry_does_not_send_when_persona_fact_conflicts(session):
    text = "Bác đi xe màu gì?"
    pipe, sender, failed = _failed_turn(session, text)
    candidate = session.query(PersonaFact).filter_by(
        persona_id="AUTO_029", fact_key="vehicle_color"
    ).one()
    assert candidate.status != "active"
    other_value = "đỏ" if candidate.value != "đỏ" else "xanh"
    session.add(PersonaFact(persona_id="AUTO_029", fact_key="vehicle_color",
                            value=other_value, source="test", status="active"))
    session.commit()

    retried = _retry(pipe, failed, text)

    assert retried.status != "sent"
    assert sender.sent == []
    assert session.get(PersonaFact, candidate.id).status != "active"
    assert session.query(PersonaFact).filter_by(
        persona_id="AUTO_029", fact_key="vehicle_color", status="active"
    ).one().value == other_value


def test_retry_does_not_send_revoked_story(session):
    persona_id = make_persona(session, "retry-revoked-story",
                              {"vehicle_model": "Hyundai Creta"})
    text = "Bác có kỷ niệm nào đáng nhớ với xe không?"
    pipe, sender, failed = _failed_turn(session, text, persona_id=persona_id)
    assert failed.saved_stories
    event_id = failed.saved_stories[0]["event_id"]
    story = session.get(PersonaStory, event_id)
    story.status = "revoked"
    session.commit()

    retried = _retry(pipe, failed, text, persona_id=persona_id)

    assert retried.status != "sent"
    assert sender.sent == []
    assert session.get(PersonaStory, event_id).status == "revoked"


def test_retry_fails_closed_if_old_draft_has_no_recovery_metadata(session):
    text = "Bác đang đi xe gì?"
    pipe, sender, failed = _failed_turn(session, text)
    draft = session.get(Message, failed.message_id)
    draft.trace = {"legacy": True}
    session.commit()

    retried = _retry(pipe, failed, text)

    assert retried.status != "sent"
    assert sender.sent == []


def test_two_simultaneous_retries_send_only_once(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'retry.db').as_posix()}",
                           connect_args={"check_same_thread": False,
                                         "timeout": 5})
    init_db(engine)
    Session = sessionmaker(bind=engine)
    with Session() as initial:
        seed(initial)
        pipe, _, failed = _failed_turn(initial, "Bác đang đi xe gì?")
        thread_id = failed.thread_id

    entered = Event()
    second_entered = Event()
    release = Event()
    lock = Lock()
    calls = []

    class BlockingSender:
        def send(self, message_id, text):
            with lock:
                calls.append((message_id, text))
                if len(calls) == 1:
                    entered.set()
                else:
                    second_entered.set()
            assert release.wait(5)
            return {"status": "sent"}

    sender = BlockingSender()

    def retry():
        with Session() as s:
            return turn(Pipeline(s, FakeProvider(), sender),
                        "Bác đang đi xe gì?", thread_id=thread_id,
                        idem="retry-key")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(retry)
            assert entered.wait(5)
            second = pool.submit(retry)
            second_entered.wait(1)
            release.set()
            outcomes = [first.result(timeout=5), second.result(timeout=5)]
        assert len(calls) == 1
        assert any(result.status == "sent" for result in outcomes)
    finally:
        release.set()
        engine.dispose()
