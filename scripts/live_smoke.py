"""Live smoke test với Luna thật qua OpenRouter — OPT-IN, tốn phí thật.

Chạy khi đã có env (file .env ở thư mục project, app tự đọc, hoặc
export biến môi trường trước khi chạy):
  .venv/bin/python scripts/live_smoke.py

Yêu cầu: LLM_PROVIDER=openrouter và OPENROUTER_API_KEY đã cấu hình.
Nếu thiếu, script dừng ngay với exit 2 và KHÔNG gọi mạng, KHÔNG in key.
Giới hạn phí theo lượt lấy từ env MAX_COST_USD_PER_TURN (mặc định 0.50 USD).

Script chỉ chạy 3 lượt trên DB in-memory + FakeSender, không gửi Facebook,
không ghi vào DB thật của sandbox.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import SETTINGS
from app.db import init_db
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.providers.openrouter import OpenRouterProvider
from app.schemas import TurnRequest
from app.seed import seed

TURNS = [
    "Bác đi xe màu gì thế?",
    "Tôi cũng muốn mua một chiếc như vậy.",
    "Giá Creta bản cao nhất bao nhiêu?",
]


def main() -> int:
    if SETTINGS.llm_provider != "openrouter" or not SETTINGS.openrouter_api_key:
        print("Chưa cấu hình live: cần LLM_PROVIDER=openrouter và "
              "OPENROUTER_API_KEY trong .env hoặc môi trường. "
              "Không gọi OpenRouter.", file=sys.stderr)
        return 2
    print(f"Model: {SETTINGS.openrouter_model} "
          f"(cap {SETTINGS.max_cost_usd_per_turn} USD/lượt)")
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    session = sessionmaker(bind=engine)()
    seed(session)
    pipe = Pipeline(session, OpenRouterProvider(), FakeSender())
    thread_id = None
    total_cost = 0.0
    for text in TURNS:
        r = pipe.run(TurnRequest(
            persona_id="AUTO_029",
            post_text="Mình mới lấy Creta đi làm hàng ngày, cuối tuần về quê.",
            post_vehicle_models=["Hyundai Creta"],
            actor_id="actor-1", text=text, thread_id=thread_id))
        thread_id = r.thread_id
        total_cost += (r.trace or {}).get("cost_usd", 0.0) or 0.0
        print(f"\n[{r.status}] Khách: {text}")
        print(f"Persona: {r.reply_text}")
        print(f"Validator: {r.validator_errors or 'OK'} | "
              f"cost: {(r.trace or {}).get('cost_usd')} | "
              f"llm_calls: {(r.trace or {}).get('llm_calls')}")
        if r.status == "error":
            print("Live smoke FAIL ở lượt trên.", file=sys.stderr)
            return 1
    print(f"\nLive smoke OK. Tổng cost ghi nhận: {total_cost}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
