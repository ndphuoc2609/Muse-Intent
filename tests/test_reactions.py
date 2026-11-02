"""Reactions kiểu Facebook: 1 reaction/actor/message, bấm lại thì gỡ,
bấm khác thì đổi, tổng hợp theo thread."""
import pytest

from app.engine.reactions import get_reactions, toggle_reaction
from app.models import Message, Thread


def _setup(session):
    t = Thread(persona_id="AUTO_029", post_id="post-1",
               post_text="Bài về Creta.", post_vehicle_models=["Hyundai Creta"])
    session.add(t)
    session.commit()
    m1 = Message(thread_id=t.id, actor_id="actor-1", role="user",
                 text="Bác đi xe màu gì thế?", status="sent")
    m2 = Message(thread_id=t.id, actor_id=None, role="persona",
                 text="Mình đi Creta trắng.", status="sent")
    session.add_all([m1, m2])
    session.commit()
    return t.id, m1.id, m2.id


def test_toggle_add_remove(session):
    tid, _, m2 = _setup(session)
    r = toggle_reaction(session, tid, m2, "actor-1", "👍")
    assert r["action"] == "added" and r["counts"] == {"👍": 1}
    assert r["mine"] == "👍"
    r = toggle_reaction(session, tid, m2, "actor-1", "👍")
    assert r["action"] == "removed" and r["counts"] == {}
    assert r["mine"] is None


def test_change_emoji_and_two_actors(session):
    tid, _, m2 = _setup(session)
    toggle_reaction(session, tid, m2, "actor-1", "👍")
    r = toggle_reaction(session, tid, m2, "actor-1", "❤️")
    assert r["action"] == "changed" and r["counts"] == {"❤️": 1}
    toggle_reaction(session, tid, m2, "actor-2", "👍")
    out = get_reactions(session, tid, "actor-1")
    assert out["counts"][m2] == {"❤️": 1, "👍": 1}
    assert out["mine"][m2] == "❤️"


def test_invalid_emoji_and_wrong_thread(session):
    tid, m1, _ = _setup(session)
    with pytest.raises(ValueError):
        toggle_reaction(session, tid, m1, "actor-1", "🔥")
    with pytest.raises(ValueError):
        toggle_reaction(session, tid, m1, "", "👍")
    with pytest.raises(LookupError):
        toggle_reaction(session, "thread-khac", m1, "actor-1", "👍")
    with pytest.raises(LookupError):
        toggle_reaction(session, tid, "message-khong-co", "actor-1", "👍")
