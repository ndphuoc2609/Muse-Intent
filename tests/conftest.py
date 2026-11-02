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
         vehicles=None, idem=None, persona_id="AUTO_029"):
    # AUTO_029: chủ xe Hyundai Creta (khớp post/vehicles mặc định).
    # Seed cũ hoang-nam/minh-anh đã bị thay bằng 200 persona AUTO_*
    # (2026-11-02); test nào cần canon đặc biệt (ngày mua, trim,
    # category/tag) thì tự tạo fixture riêng, không dựa vào seed.
    req = TurnRequest(
        persona_id=persona_id, post_text=post,
        post_vehicle_models=vehicles or ["Hyundai Creta"],
        actor_id=actor, text=text, thread_id=thread_id)
    return pipe.run(req, idempotency_key=idem)


def make_persona(session, persona_id, facts=None, display_name="Test Persona"):
    """Fixture persona tối thiểu cho test cần canon đặc biệt (ngày mua,
    trim, xe đầu đời...) mà seed 200 AUTO_* không có. Chỉ dùng trong
    test, không phải seed thật."""
    from app.models import Persona, PersonaFact
    if session.get(Persona, persona_id) is None:
        session.add(Persona(
            id=persona_id, display_name=display_name, age=None, region="",
            stance="peer_user", style="Nói tự nhiên, xưng mình/bạn.",
            identity_note="PERSONA HƯ CẤU cho test.",
            is_fictional=True))
    for key, value in (facts or {}).items():
        row = session.query(PersonaFact).filter_by(
            persona_id=persona_id, fact_key=key, status="active").first()
        if row is None:
            session.add(PersonaFact(
                persona_id=persona_id, fact_key=key, value=value,
                source="test"))
    session.commit()
    return persona_id
