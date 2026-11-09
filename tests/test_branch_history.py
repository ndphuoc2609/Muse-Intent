"""Branch-based context (Phase 2a): có parent_message_id thì history
chỉ lấy nhánh đó — chuỗi cha của parent + lượt của actor hiện tại +
reply đã hiển thị trong nhánh; draft/error và nhánh của actor khác
không vào. Không parent thì giữ hành vi cũ (20 message gần nhất).
FakeProvider + SQLite in-memory, KHÔNG gọi OpenRouter."""
from datetime import datetime, timedelta

from sqlalchemy import select

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline, _branch_history
from app.models import Message, Thread
from app.providers.fake import FakeProvider
from app.schemas import TurnRequest
from conftest import turn


def _msg(session, thread_id, role, text, actor_id=None, parent=None,
         status="sent", minute=0):
    m = Message(thread_id=thread_id, role=role, actor_id=actor_id,
                text=text, status=status, parent_message_id=parent,
                created_at=datetime(2026, 11, 9, 10, 0) + timedelta(minutes=minute))
    session.add(m)
    session.commit()
    return m


def _thread_with_branches(session):
    t = Thread(persona_id="AUTO_029", post_id="post-1",
               post_text="Bài về Creta.",
               post_vehicle_models=["Hyundai Creta"])
    session.add(t)
    session.commit()
    # Nhánh 1 (actor-1): m1 -> p1 -> m3 -> p3.
    m1 = _msg(session, t.id, "user", "Bác đi xe gì?", "actor-1", minute=1)
    p1 = _msg(session, t.id, "persona", "Creta.", parent=m1.id, minute=2)
    m3 = _msg(session, t.id, "user", "Đi phố ổn không?", "actor-1",
              parent=m1.id, minute=5)
    p3 = _msg(session, t.id, "persona", "Ổn lắm bạn.", parent=m3.id, minute=6)
    # Nhánh 2 (actor-2): m2 -> p2.
    m2 = _msg(session, t.id, "user", "Giá bao nhiêu?", "actor-2", minute=3)
    p2 = _msg(session, t.id, "persona", "Giá thì mình chưa rõ.", parent=m2.id,
              minute=4)
    # Nháp + lỗi trong nhánh 1: chưa hiển thị, không vào history.
    _msg(session, t.id, "persona", "nháp chưa gửi", parent=m1.id,
         status="draft", minute=7)
    _msg(session, t.id, "persona", "lỗi chưa gửi", parent=m1.id,
         status="error", minute=8)
    # Lượt gốc khác của actor-1 (ngoài nhánh 1).
    m4 = _msg(session, t.id, "user", "Bác mua xe khi nào?", "actor-1", minute=9)
    return t, {"m1": m1, "p1": p1, "m3": m3, "p3": p3,
               "m2": m2, "p2": p2, "m4": m4}


def test_branch_history_scopes_to_parent_branch(session):
    t, m = _thread_with_branches(session)
    rows = _branch_history(session, t.id, "actor-1", m["m3"].id)
    ids = [r.id for r in rows]
    assert ids == [m["m1"].id, m["p1"].id, m["m3"].id, m["p3"].id, m["m4"].id]


def test_branch_history_excludes_other_actor_branch_and_unsent(session):
    t, m = _thread_with_branches(session)
    rows = _branch_history(session, t.id, "actor-2", m["m2"].id)
    ids = [r.id for r in rows]
    # Nhánh 2 + lượt của actor-2; không lấy nhánh 1 của actor-1.
    assert ids == [m["m2"].id, m["p2"].id]
    texts = [r.text for r in rows]
    assert "nháp chưa gửi" not in texts and "lỗi chưa gửi" not in texts


def test_branch_history_without_parent_keeps_last_20(session):
    t, m = _thread_with_branches(session)
    rows = _branch_history(session, t.id, "actor-1", None)
    # Toàn bộ thread theo thời gian (ít hơn 20 nên lấy hết).
    assert [r.text for r in rows] == [
        "Bác đi xe gì?", "Creta.", "Giá bao nhiêu?",
        "Giá thì mình chưa rõ.", "Đi phố ổn không?", "Ổn lắm bạn.",
        "nháp chưa gửi", "lỗi chưa gửi", "Bác mua xe khi nào?"]


def test_turn_with_parent_links_messages(pipe, session):
    r1 = turn(pipe, "Bác đi xe gì?")
    assert r1.status == "sent"
    user_msg = session.execute(
        select(Message).where(Message.thread_id == r1.thread_id,
                             Message.role == "user")).scalars().first()
    req = TurnRequest(persona_id="AUTO_029", post_text="Bài về Creta.",
                      post_vehicle_models=["Hyundai Creta"],
                      actor_id="actor-1", text="Đi phố ổn không bác?",
                      thread_id=r1.thread_id,
                      parent_message_id=user_msg.id)
    r2 = pipe.run(req)
    assert r2.status == "sent"
    user2 = session.execute(
        select(Message).where(Message.thread_id == r1.thread_id,
                             Message.role == "user",
                             Message.text == "Đi phố ổn không bác?")
    ).scalars().first()
    assert user2.parent_message_id == user_msg.id
    reply2 = session.get(Message, r2.message_id)
    assert reply2.parent_message_id == user2.id
