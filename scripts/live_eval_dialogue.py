"""Live evaluation hội thoại tự nhiên (OPT-IN, Luna qua OpenRouter) —
bộ case mới theo brief 30/10, bổ sung cho scripts/live_eval.py (không
thay thế tập cũ).

Chạy chỉ khi đủ điều kiện, nếu không thoát mã 2 và KHÔNG tốn phí:
  LIVE_EVAL_DIALOGUE=1 OPENROUTER_API_KEY=<redacted> OPENROUTER_MODEL=openai/gpt-6-luna \\
    .venv/bin/python scripts/live_eval_dialogue.py

Ngân sách: dừng ngay khi tổng cost đã biết vượt LIVE_EVAL_BUDGET_USD
(mặc định 1.0). Cost provider không trả về được ghi là unknown, không
coi là 0. Persona minh-anh chỉ dùng ở case tucson_vague_buy (cần story
Tucson đã seed); các case còn lại dùng hoang-nam + bài Creta mặc định.

Kết quả: runs/live_eval_dialogue.json — transcript thô + status,
latency, cost, validator_errors, planner goal để HUMAN review theo các
thuộc tính hành vi (đúng ý, đủ ý, liền mạch, không hỏi lặp, không
nhầm chủ thể, không chặn nhầm). Script KHÔNG tự chấm naturalness bằng
model judge và KHÔNG tự tuyên bố đạt chất lượng người dùng thật.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# (tên case, persona, post vehicles, [lượt khách])
CASES = [
    ("two_facts_one_turn", "hoang-nam", ["Hyundai Creta"],
     ["Bác đang đi xe gì và mua lúc nào?"]),
    ("interest_not_transaction", "hoang-nam", ["Hyundai Creta"],
     ["Tôi cũng muốn mua một chiếc như vậy."]),
    ("short_answer_priority", "hoang-nam", ["Hyundai Creta"],
     ["Tôi cũng muốn mua một chiếc như vậy.",
      "Tiết kiệm thôi, đang tính kiếm đại lý để xem xe ạ."]),
    ("topic_switch_keeps_pending", "hoang-nam", ["Hyundai Creta"],
     ["Tôi cũng muốn mua một chiếc như vậy.",
      "Bác đi xe gì?"]),
    ("third_party_correction", "hoang-nam", ["Hyundai Creta"],
     ["Mình mua cho bố.",
      "À không, mua cho mình."]),
    ("browse_only", "hoang-nam", ["Hyundai Creta"],
     ["Mình chỉ xem thôi."]),
    ("partial_answer_missing_product", "hoang-nam", ["Hyundai Santa Fe"],
     ["Bác đi xe gì và con này giá bao nhiêu?"]),
    ("color_propose_and_recall", "hoang-nam", ["Hyundai Creta"],
     ["Bác đi xe màu gì thế?",
      "Thế màu xe bác là gì nhỉ?"]),
    ("tucson_vague_buy", "minh-anh", ["Hyundai Creta"],
     ["Đi những đâu rồi",
      "Tôi cũng tính lấy 1 chiếc"]),
    ("long_thread_recall", "hoang-nam", ["Hyundai Creta"],
     ["Chào bạn.", "Chào bạn.", "Chào bạn.", "Chào bạn.", "Chào bạn.",
      "Chào bạn.", "Bác đi xe gì?"]),
]


def main():
    if os.environ.get("LIVE_EVAL_DIALOGUE") != "1":
        print("LIVE_EVAL_DIALOGUE != 1: không chạy live, không tốn phí. "
              "Đặt LIVE_EVAL_DIALOGUE=1 + OPENROUTER_API_KEY để chạy.",
              file=sys.stderr)
        sys.exit(2)
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("Thiếu OPENROUTER_API_KEY: không chạy live.", file=sys.stderr)
        sys.exit(2)
    budget = float(os.environ.get("LIVE_EVAL_BUDGET_USD", "1.0"))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import init_db
    from app.engine.fake_sender import FakeSender
    from app.engine.pipeline import Pipeline
    from app.providers.openrouter import OpenRouterProvider
    from app.schemas import TurnRequest
    from app.seed import seed

    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    s = sessionmaker(bind=engine)()
    seed(s)
    pipe = Pipeline(s, OpenRouterProvider(), FakeSender())
    rows = []
    total_known = 0.0
    for name, persona, vehicles, turns in CASES:
        thread_id = None
        case_rows = []
        for text in turns:
            t0 = time.time()
            r = pipe.run(TurnRequest(
                persona_id=persona, post_text="Bài về Creta.",
                post_vehicle_models=vehicles,
                actor_id="actor-1", text=text, thread_id=thread_id))
            thread_id = r.thread_id
            cost = (r.trace or {}).get("cost_usd") or 0.0
            total_known += cost
            case_rows.append({
                "input": text, "status": r.status, "reply": r.reply_text,
                "planner_goal": (r.planner or {}).get("goal"),
                "resolved_to": ((r.planner or {}).get("resolved_reference")
                                or {}).get("resolved_to"),
                "latency_s": round(time.time() - t0, 3),
                "cost_usd": cost,
                "cost_known": (r.trace or {}).get("cost_known"),
                "validator_errors": r.validator_errors,
                "saved_customer_facts": r.saved_customer_facts,
            })
            if total_known > budget:
                print(f"Vượt ngân sách live {budget} USD, dừng.",
                      file=sys.stderr)
                break
        rows.append({"case": name, "turns": case_rows})
        if total_known > budget:
            break
    dest = Path(__file__).resolve().parents[1] / "runs" / "live_eval_dialogue.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps({"mode": "live_openrouter",
                                "budget_usd": budget,
                                "total_known_cost_usd": round(total_known, 6),
                                "rows": rows}, ensure_ascii=False, indent=2))
    print(f"Xong live eval dialogue: {len(rows)} case, cost đã biết "
          f"{total_known:.4f} USD. "
          "Cần human review transcript trước khi kết luận naturalness.")


if __name__ == "__main__":
    main()
