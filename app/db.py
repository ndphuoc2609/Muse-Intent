"""DB engine + session. SQLite mặc định; Postgres khi DATABASE_URL bắt
đầu bằng postgresql:// (Neon dùng đúng dạng này, cần sslmode=require
trong URL nếu Neon yêu cầu)."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import SETTINGS
from .models import Base


def make_engine(url: str | None = None):
    url = url or SETTINGS.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    return create_engine(url, connect_args=connect_args, future=True)


def init_db(engine) -> None:
    """Tạo bảng nếu chưa có. Tương đương migration 001 (xem migrations/)."""
    Base.metadata.create_all(engine)


def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False,
                        future=True)
