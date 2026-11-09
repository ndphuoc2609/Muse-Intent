"""A stale or unidentifiable draft must never be sent on retry."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import init_db
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.models import CustomerFact, Message, PersonaStory
from app.providers.fake import FakeProvider
from app.seed import seed
from tests.conftest import turn


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


def test_retry_does_not_send_when_customer_fact_conflicts(session):
    # In-turn propose_persona_fact đã gỡ: xung đột canon giờ đi qua
    # customer fact — một fact current cùng predicate xuất hiện sau khi
    # draft stage thì retry không được gửi.
    from test_delivery_recovery import BuyingInterestProvider

    text = "Em đang tính mua Creta"
    sender = FakeSender(fail_next=True)
    pipe = Pipeline(session, BuyingInterestProvider(), sender)
    failed = turn(pipe, text, idem="retry-key")
    assert failed.status == "error"
    assert sender.sent == []
    candidate = session.query(CustomerFact).filter_by(
        thread_id=failed.thread_id, actor_id="actor-1",
        predicate="buying_interest").one()
    assert candidate.status != "current"
    session.add(CustomerFact(thread_id=failed.thread_id, actor_id="actor-1",
                             subject="self", predicate="buying_interest",
                             value_raw="chỉ xem thôi",
                             evidence="chỉ xem thôi",
                             source="stated", status="current"))
    session.commit()

    retried = _retry(pipe, failed, text)

    assert retried.status != "sent"
    assert sender.sent == []
    assert session.get(CustomerFact, candidate.id).status != "current"
    assert session.query(CustomerFact).filter_by(
        thread_id=failed.thread_id, actor_id="actor-1",
        predicate="buying_interest", status="current"
    ).one().value_raw == "chỉ xem thôi"


def test_retry_does_not_send_revoked_story(session):
    # Story kể từ canon seed (AUTO_029), không còn tự tạo story mới
    # trong lượt: event_id lấy từ claim trong snapshot của draft.
    text = "Bác có kỷ niệm nào đáng nhớ với xe không?"
    pipe, sender, failed = _failed_turn(session, text)
    claims = (failed.trace or {}).get("delivery_recovery", {}).get(
        "final", {}).get("claims", [])
    story_claims = [c for c in claims if c.get("kind") == "story"]
    assert story_claims, "failed turn phải claim một story canon"
    event_id = story_claims[0]["ref_id"]
    story = session.get(PersonaStory, event_id)
    story.status = "revoked"
    session.commit()

    retried = _retry(pipe, failed, text)

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
