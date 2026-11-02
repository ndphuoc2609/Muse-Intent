"""Đánh giá synthetic nhiều lượt (§19) — chạy offline với FakeProvider.

KHÔNG coi kết quả này là bằng chứng tự nhiên với khách thật: Fake chỉ
mô phỏng, và các câu synthetic do chính nhóm viết. Mục đích: bắt hồi
quy hành vi (đúng câu hỏi, đúng chủ thể, không hỏi lặp, không handoff
nhầm, không dùng fact sai) trước khi chạy live evaluation với Luna thật.

Chạy: .venv/bin/python scripts/eval_synthetic.py
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

# (input, kỳ vọng: status, chuỗi phải có trong reply, chủ thể customer
#  fact, chuỗi KHÔNG được có trong reply). Seed mới không có
#  purchased_at nên case mua xe đổi thành: không được bịa ngày mua.
CASES = [
    ("Bác đang đi xe gì?", "sent", "Creta", None, None),
    ("Bác mua xe lúc nào?", "sent", None, None, "2024"),
    ("Bác đi xe màu gì?", "sent", "trắng", None, None),
    ("Giá Creta bản cao nhất bao nhiêu?", "sent", "599", None, None),
    ("Tôi muốn đặt cọc ngay.", "review", "không xác nhận", None, None),
    ("Thôi, mình không muốn tiếp tục nữa.", "sent", "dừng", None, None),
    ("Em đang cân nhắc con này cho mẹ.", "sent", None, "third_person", None),
    ("Mình chủ yếu chở gia đình đi xa.", "sent", None, "self", None),
    ("Bác có phải AI không?", "sent", "không phải người thật", None, None),
    # AUTO_029 kể lại memory đầu tiên (chó Golden), không còn trip Nam Định.
    ("Bác có kỷ niệm nào đáng nhớ với xe không?", "sent", "Golden", None, None),
]


def main():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    session = sessionmaker(bind=engine)()
    seed(session)
    pipe = Pipeline(session, FakeProvider(), FakeSender())
    rows = []
    for text, exp_status, exp_substr, exp_subject, exp_absent in CASES:
        r = pipe.run(TurnRequest(
            persona_id="AUTO_029", post_text="Bài về Creta.",
            post_vehicle_models=["Hyundai Creta"],
            actor_id="actor-1", text=text))
        ok_status = r.status == exp_status
        ok_text = exp_substr is None or exp_substr in (r.reply_text or "")
        ok_subject = exp_subject is None or any(
            f["subject"] == exp_subject for f in r.saved_customer_facts)
        ok_absent = exp_absent is None or exp_absent not in (r.reply_text or "")
        rows.append({"input": text, "status": r.status,
                     "expected_status": exp_status, "reply": r.reply_text,
                     "pass": bool(ok_status and ok_text and ok_subject
                                  and ok_absent)})
        print(f"{'PASS' if rows[-1]['pass'] else 'FAIL'} | {text} -> "
              f"[{r.status}] {r.reply_text}")
    n = sum(r["pass"] for r in rows)
    print(f"\n{n}/{len(rows)} pass. Lưu ý: synthetic + Fake, không phải bằng "
          f"chứng với khách thật (§19).")
    dest = Path(__file__).resolve().parents[1] / "runs" / "eval_synthetic.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
