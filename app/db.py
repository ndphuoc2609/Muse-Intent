"""DB engine + session. SQLite mặc định; Postgres khi DATABASE_URL bắt
đầu bằng postgresql://.

Lỗi gốc cậu gặp: SQLAlchemy 2.0 với URL postgresql:// mặc định dùng
driver psycopg (v3), nhưng requirements.txt cũ không khai báo psycopg
nên create_engine fail ngay với ModuleNotFoundError. Đã sửa bằng cách
thêm psycopg[binary] vào requirements.txt (xem dưới) và, nếu driver vẫn
thiếu, báo lỗi rõ ràng kèm lệnh cài thay vì traceback dài của SQLAlchemy.

Neon: host *.neon.tech bắt buộc SSL, nên nếu URL Neon chưa có sslmode
thì tự thêm sslmode=require. Postgres local (docker-compose) không bị
ép SSL, để không phá setup local.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import SETTINGS
from .models import Base


def _normalize_postgres_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "neon.tech" in url \
            and "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url


def make_engine(url: str | None = None):
    url = _normalize_postgres_url(url or SETTINGS.database_url)
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    try:
        return create_engine(url, connect_args=connect_args, future=True)
    except ModuleNotFoundError as e:
        if "psycopg" in str(e):
            raise RuntimeError(
                "Thiếu driver Postgres (psycopg). Chạy trong venv đang "
                "dùng rồi khởi động lại server:\n"
                "  pip install 'psycopg[binary]'\n"
                "Lỗi gốc: ModuleNotFoundError: No module named 'psycopg'. "
                "Không tự fallback sang SQLite khi cậu đã cấu hình "
                "DATABASE_URL Postgres, để tránh ghi nhầm dữ liệu sang DB khác."
            ) from e
        raise


def init_db(engine) -> None:
    """Tạo bảng nếu chưa có. Tương đương migration 001 (xem migrations/)."""
    Base.metadata.create_all(engine)


def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False,
                        future=True)
