"""Pool Postgres chống connection chết do idle (Neon suspend compute
sau ~5 phút): checkout phải ping trước, recycle trước 5 phút.
Không cần server Postgres thật — create_engine chưa kết nối."""
from app.db import make_engine


def test_postgres_engine_pre_ping_and_recycle():
    eng = make_engine("postgresql://user:pass@localhost:5432/db")
    assert eng.pool._pre_ping is True
    assert eng.pool._recycle == 280


def test_sqlite_engine_unaffected():
    eng = make_engine("sqlite:///:memory:")
    assert getattr(eng.pool, "_pre_ping", False) is False
