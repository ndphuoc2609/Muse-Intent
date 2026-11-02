"""Chạy transcript 7 kịch bản (§20.8) qua pipeline THẬT với FakeProvider.

Mỗi lượt in: input, planner, tool call, dữ kiện được lưu, validator, reply.
Output: runs/transcript.json (đọc được, không chứa key hay dữ liệu thật).
Chạy: .venv/bin/python scripts/make_transcript.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import init_db
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.providers.fake import FakeProvider
from app.schemas import TurnRequest
from app.seed import seed

TURNS = [
    ("Hỏi màu xe", "Bác đi xe màu gì thế?"),
    ("Quan tâm mua xe", "Tôi cũng muốn mua một chiếc như vậy."),
    ("Hỏi hộ người khác", "Em đang cân nhắc con này cho bố."),
    ("Tạo story (hỏi kỷ niệm)", "Bác có kỷ niệm nào đáng nhớ với xe không?"),
    ("Truy xuất story cũ", "Kể lại chuyến đi đó đi bác."),
    ("Bổ sung addendum", "Đi cùng ai thế bác?"),
    ("Hỏi product fact", "Giá Creta bản cao nhất bao nhiêu?"),
    ("Khách muốn dừng", "Thôi, mình không muốn tiếp tục nữa."),
]


def main():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    session = sessionmaker(bind=engine)()
    seed(session)
    pipe = Pipeline(session, FakeProvider(), FakeSender())
    out = []
    thread_id = None
    for label, text in TURNS:
        r = pipe.run(TurnRequest(
            persona_id="AUTO_029",
            post_text="Mình mới lấy Creta đi làm hàng ngày, cuối tuần về quê.",
            post_vehicle_models=["Hyundai Creta"],
            actor_id="actor-1", text=text, thread_id=thread_id))
        thread_id = r.thread_id
        item = {
            "label": label, "input": text, "status": r.status,
            "planner": r.planner, "tool_calls": r.tool_calls,
            "saved_customer_facts": r.saved_customer_facts,
            "saved_stories": r.saved_stories,
            "validator_errors": r.validator_errors,
            "reply": r.reply_text,
        }
        out.append(item)
        print(f"\n=== {label} [{r.status}] ===")
        print(f"Khách: {text}")
        print(f"Persona: {r.reply_text}")
        print(f"Planner intents: "
              f"{[i['type'] + ':' + i['normalized'] for i in r.planner['intents']]}")
        print(f"Tool calls: {[t['tool'] for t in r.tool_calls]}")
        print(f"Lưu customer: {r.saved_customer_facts}")
        print(f"Lưu story: {r.saved_stories}")
        print(f"Validator: {r.validator_errors or 'OK'}")
    dest = Path(__file__).resolve().parents[1] / "runs" / "transcript.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nĐã ghi {dest}")


if __name__ == "__main__":
    main()
