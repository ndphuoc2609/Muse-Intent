"""Eval persona-depth bằng FakeProvider (offline, không phải bằng chứng
Luna thật). Kiểm tra: isolation hai persona, story có tags/categories,
không gán nhu cầu 7 chỗ từ tag family, không hỏi lặp, validator đạt.

Chạy: .venv/bin/python scripts/eval_persona_depth.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import init_db
from app.engine.fake_sender import FakeSender
from app.engine.persona_validator import validate_persona
from app.engine.pipeline import Pipeline
from app.providers.fake import FakeProvider
from app.schemas import TurnRequest
from app.seed import seed


def make_pipe():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    s = sessionmaker(bind=engine)()
    seed(s)
    return s, Pipeline(s, FakeProvider(), FakeSender())


def turn(pipe, persona, text, thread_id=None, actor="actor-1"):
    req = TurnRequest(persona_id=persona, post_text="Bài về xe.",
                      post_vehicle_models=["Hyundai Creta"],
                      actor_id=actor, text=text, thread_id=thread_id)
    return pipe.run(req)


def main():
    s, pipe = make_pipe()
    cases = []

    def check(name, cond, detail=""):
        cases.append((name, bool(cond), detail))
        print(("PASS" if cond else "FAIL"), "-", name,
              ("| " + detail if detail and not cond else ""))

    # 1. Validator đạt cho cả hai persona (không error).
    for pid in ("hoang-nam", "minh-anh"):
        v = validate_persona(s, pid)
        check(f"validator {pid} không có error", not v["errors"],
              str(v["errors"]))

    # 2. Isolation: cùng câu hỏi, hai persona trả khác nhau (xe khác nhau).
    r_nam = turn(pipe, "hoang-nam", "Bác đang đi xe gì?")
    r_anh = turn(pipe, "minh-anh", "Bác đang đi xe gì?")
    check("isolation: Nam nói Creta, Anh nói Tucson",
          "Creta" in (r_nam.reply_text or "")
          and "Tucson" in (r_anh.reply_text or ""))

    # 3. Story có tags/categories và active sau sent.
    r = turn(pipe, "hoang-nam", "Bác có kỷ niệm nào đáng nhớ với xe không?")
    ok = bool(r.saved_stories and r.saved_stories[0].get("tags")
              and r.saved_stories[0].get("story_categories"))
    check("story propose có tags + categories", ok)

    # 4. Không gán nhu cầu 7 chỗ từ câu chở gia đình.
    r2 = turn(pipe, "hoang-nam", "Mình chủ yếu chở gia đình đi xa.")
    check("không gán 7 chỗ từ câu gia đình",
          "7 chỗ" not in (r2.reply_text or "")
          and not any("7 chỗ" in (f.get("value_normalized") or "")
                      for f in r2.saved_customer_facts))

    # 5. Không hỏi lặp sau câu trả lời ngắn.
    r3 = turn(pipe, "hoang-nam", "Cũng tính lấy xe về đi phố bác ạ")
    r4 = turn(pipe, "hoang-nam", "Tiết kiệm thôi, đang tính kiếm đại lý "
                                 "để xem xe ạ", thread_id=r3.thread_id)
    check("không hỏi lặp câu ưu tiên ở lượt 5",
          r4.reply_text != r3.reply_text)

    # 6. Seed idempotent: gọi lại không tạo trùng persona.
    before = s.query(__import__("app.models", fromlist=["Persona"]).Persona).count()
    seed(s)
    after = s.query(__import__("app.models", fromlist=["Persona"]).Persona).count()
    check("seed idempotent (số persona không đổi)", before == after,
          f"{before} -> {after}")

    n_pass = sum(1 for _, ok, _ in cases if ok)
    print(f"\n{n_pass}/{len(cases)} pass (Fake only, không phải bằng chứng Luna).")
    return 0 if n_pass == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
