"""Configuration. No secrets here: everything sensitive comes from env only.

Giả định ghi rõ (xem README § Giả định):
- LLM mặc định là FakeProvider để sandbox/test chạy offline, không tốn phí.
  Khi OPENROUTER_API_KEY + OPENROUTER_MODEL được cấu hình, provider
  chuyển sang OpenRouter (mặc định model Luna: openai/gpt-6-luna).
- DB mặc định SQLite local; DATABASE_URL postgresql://... (Neon) thì dùng Postgres.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    """Đọc file .env (nếu có) mà không cần dependency python-dotenv.

    Bản zip đầu tiên chưa tự đọc .env nên `uvicorn` chạy vẫn ở Fake dù
    đã tạo .env — bản này đã sửa: chỉ cần đặt .env cạnh project (hoặc
    ở thư mục chạy lệnh) rồi chạy uvicorn/script là đủ. Biến môi trường
    thật luôn thắng .env (không ghi đè), và giá trị key không bao giờ
    được in ra log.
    """
    for candidate in (Path.cwd() / ".env",
                      Path(__file__).resolve().parents[1] / ".env"):
        if not candidate.is_file():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip("\"'")
            os.environ.setdefault(key, value)


_load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    database_url: str = os.environ.get(
        "DATABASE_URL", "sqlite:///./persona-dialogue.db"
    )
    llm_provider: str = os.environ.get("LLM_PROVIDER", "fake").lower()
    openrouter_api_key: str = os.environ.get("OPENROUTER_API_KEY", "")
    openrouter_model: str = os.environ.get(
        "OPENROUTER_MODEL", "openai/gpt-6-luna"
    )
    openrouter_base_url: str = os.environ.get(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    )
    llm_timeout_s: float = _float("LLM_TIMEOUT_S", 30.0)
    llm_max_retries: int = _int("LLM_MAX_RETRIES", 2)
    max_tool_rounds: int = _int("MAX_TOOL_ROUNDS", 6)
    max_validator_repairs: int = _int("MAX_VALIDATOR_REPAIRS", 2)
    max_llm_calls_per_turn: int = _int("MAX_LLM_CALLS_PER_TURN", 10)
    max_cost_usd_per_turn: float = _float("MAX_COST_USD_PER_TURN", 0.50)
    max_stories_per_turn: int = _int("MAX_STORIES_PER_TURN", 2)
    max_stories_per_day_per_persona: int = _int(
        "MAX_STORIES_PER_DAY_PER_PERSONA", 20
    )
    # Persona hư cấu: khi khách hỏi trực tiếp "có phải AI không", phải nói rõ.
    ai_disclosure: str = os.environ.get(
        "AI_DISCLOSURE",
        "Mình là persona hư cấu do hệ thống tạo ra để chia sẻ trải nghiệm, "
        "không phải người thật.",
    )
    fake_sender_fail_next: bool = (
        os.environ.get("FAKE_SENDER_FAIL_NEXT", "") == "1"
    )
    # Partner mode (Phase 3): khi PARTNER_API_KEY được cấu hình thì các
    # endpoint partner (/turns, /threads/*/turns, xác nhận gửi) yêu cầu
    # header X-API-Key, thread bị giới hạn theo tenant, và reply chỉ ở
    # trạng thái ready_for_delivery cho tới khi partner xác nhận đã gửi
    # trên kênh thật. Không cấu hình key = sandbox như cũ (FakeSender
    # gửi mô phỏng, không cần xác nhận). Xem docs/api-integration.md.
    partner_api_key: str = os.environ.get("PARTNER_API_KEY", "")
    partner_tenant: str = os.environ.get("PARTNER_TENANT", "default")

    @property
    def partner_mode(self) -> bool:
        return bool(self.partner_api_key)


SETTINGS = Settings()

# Fact key bị khóa: model không được đề xuất sửa, để tránh hợp thức hóa
# một câu chuyện bằng cách sửa hồ sơ nền (§8, §9).
LOCKED_FACT_KEYS = {
    "vehicle_model", "vehicle_trim", "purchased_at", "is_first_car",
    "display_name", "region",
}
# Fact key model được phép đề xuất tạo mới (còn lại -> engine từ chối,
# trả về review thay vì tự bịa).
CREATABLE_FACT_KEY_PREFIXES = (
    "vehicle_color", "favorite_", "trip_", "commute_", "weekend_",
    "experience_", "habit_", "memory_",
)
