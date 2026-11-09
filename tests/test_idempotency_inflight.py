"""An in-flight request owns its scoped idempotency key until completion."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import init_db
from app.engine.pipeline import Pipeline
from app.models import Thread
from app.providers.fake import FakeProvider
from app.seed import seed
from tests.conftest import turn


def test_inflight_request_same_key_does_not_call_provider_or_sender(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'inflight.db').as_posix()}",
                           connect_args={"check_same_thread": False,
                                         "timeout": 5})
    init_db(engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        seed(session)
        thread = Thread(persona_id="AUTO_029", post_id="post-1",
                        post_text="Bài về Creta.",
                        post_vehicle_models=["Hyundai Creta"])
        session.add(thread)
        session.commit()
        thread_id = thread.id

    entered = Event()
    release = Event()
    lock = Lock()
    sent = []

    class BlockingSender:
        def send(self, message_id, text):
            with lock:
                sent.append((message_id, text))
                number = len(sent)
            if number == 1:
                entered.set()
                assert release.wait(5)
            return {"status": "sent"}

    class CountingProvider(FakeProvider):
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            return super().chat(messages, tools)

    sender = BlockingSender()
    provider_first = CountingProvider()
    provider_second = CountingProvider()

    def run(provider):
        with Session() as session:
            pipe = Pipeline(session, provider, sender)
            return turn(pipe, "Bác đang đi xe gì?", thread_id=thread_id,
                        idem="inflight-key")

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run, provider_first)
            try:
                assert entered.wait(5)
                second = pool.submit(run, provider_second)
                competing = second.result(timeout=5)
                assert competing.status == "error"
                assert any("in_progress" in error or "đang" in error.lower()
                           for error in competing.validator_errors)
                assert provider_second.calls == 0
                assert len(sent) == 1
            finally:
                release.set()
            assert first.result(timeout=5).status == "sent"
    finally:
        release.set()
        engine.dispose()
