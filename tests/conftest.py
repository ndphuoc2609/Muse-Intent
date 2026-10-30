"""Test dùng FakeProvider + SQLite in-memory. KHÔNG gọi OpenRouter."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import init_db
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.providers.fake import FakeProvider
from app.schemas import TurnRequest
from app.seed import seed


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    seed(s)
    yield s
    s.close()


@pytest.fixture()
def pipe(session):
    return Pipeline(session, FakeProvider(), FakeSender())


def turn(pipe, text, thread_id=None, actor="actor-1", post="Bài về Creta.",
         vehicles=None, idem=None):
    req = TurnRequest(
        persona_id="hoang-nam", post_text=post,
        post_vehicle_models=vehicles or ["Hyundai Creta"],
        actor_id=actor, text=text, thread_id=thread_id)
    return pipe.run(req, idempotency_key=idem)
