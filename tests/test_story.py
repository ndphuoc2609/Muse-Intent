from sqlalchemy import select

from app.engine.tools import ToolExecutor
from app.models import PersonaStory
from conftest import turn


def test_create_and_recall_story(pipe, session):
    from conftest import make_persona
    pid = make_persona(session, "fix-no-stories",
                       {"vehicle_model": "Hyundai Creta"})
    r = turn(pipe, "Bác có kỷ niệm nào đáng nhớ với xe không?",
             persona_id=pid)
    assert r.status == "sent"
    assert r.saved_stories, "story phải được lưu trước khi gửi"
    event_id = r.saved_stories[0]["event_id"]
    r2 = turn(pipe, "Kể lại chuyến đi đó đi bác.", thread_id=r.thread_id,
              persona_id=pid)
    assert r2.status == "sent"
    # Lượt sau phải tái sử dụng story cũ, không tạo story mới trùng.
    assert not r2.saved_stories or all(
        s["event_id"] == event_id for s in r2.saved_stories)


def test_same_story_rephrased_reuses_event_id(pipe, session):
    ex = ToolExecutor(session, "hoang-nam", "t", "actor-1")
    a = ex.execute("propose_story", {
        "title": "Chuyến về Nam Định", "summary": "Đi Creta về Nam Định cuối tuần",
        "body": "Cuối tuần mình lái Creta về Nam Định, đường êm, cả nhà thoải mái.",
        "event_type": "trip",
        "entities": {"vehicle": "Hyundai Creta", "places": ["Nam Định"]}})
    b = ex.execute("propose_story", {
        "title": "Về quê cuối tuần", "summary": "Lái Creta về Nam Định cuối tuần",
        "body": "Mình lái Creta về Nam Định cuối tuần, đường trường êm, nhà ngồi thoải mái.",
        "event_type": "trip",
        "entities": {"vehicle": "Hyundai Creta", "places": ["Nam Định"]}})
    assert a.data["event_id"] == b.data["event_id"]
    assert b.data.get("reused") is True


def test_addendum_and_conflicting_addendum(pipe, session):
    ex = ToolExecutor(session, "hoang-nam", "t", "actor-1")
    s = ex.execute("propose_story", {
        "title": "Chuyến về quê", "summary": "Đi Creta về Nam Định",
        "body": "Mình lái Creta về Nam Định.",
        "event_type": "trip",
        "entities": {"vehicle": "Hyundai Creta", "places": ["Nam Định"]}})
    eid = s.data["event_id"]
    ok = ex.execute("propose_addendum", {
        "event_id": eid, "content": "Hôm đó đi cùng cả nhà.",
        "entities": {"persons": ["gia đình"]}})
    assert ok.status == "FOUND"
    bad = ex.execute("propose_addendum", {
        "event_id": eid, "content": "Thực ra không phải Nam Định mà là nơi khác.",
        "entities": {}})
    assert bad.status == "CONFLICT"


def test_timeline_before_purchase_rejected(pipe, session):
    from conftest import make_persona
    make_persona(session, "fix-before-purchase", {
        "vehicle_model": "Hyundai Creta", "purchased_at": "2024-03"})
    ex = ToolExecutor(session, "fix-before-purchase", "t", "actor-1")
    r = ex.execute("propose_story", {
        "title": "Đi xe trước khi mua", "summary": "Đi Creta từ 2023",
        "body": "Mình đi Creta từ 2023.",
        "event_type": "trip", "event_date": "2023-06-01",
        "event_date_precision": "day",
        "entities": {"vehicle": "Hyundai Creta"}})
    assert r.status == "CONFLICT"


def test_two_personas_isolated(pipe, session):
    from app.models import Persona
    session.add(Persona(id="persona-b", display_name="Người B",
                        region="Hà Nội", stance="peer_user"))
    session.commit()
    ex_a = ToolExecutor(session, "hoang-nam", "t", "actor-1")
    ex_b = ToolExecutor(session, "persona-b", "t", "actor-1")
    s = ex_a.execute("propose_story", {
        "title": "Chuyến A", "summary": "Đi Creta về quê",
        "body": "Mình đi Creta về quê.", "event_type": "trip",
        "entities": {"vehicle": "Hyundai Creta", "places": ["Nam Định"]}})
    got = ex_b.execute("propose_addendum", {
        "event_id": s.data["event_id"], "content": "Thêm chi tiết.",
        "entities": {}})
    assert got.status == "NOT_FOUND"


def test_revoke_story(pipe, session):
    from conftest import make_persona
    pid = make_persona(session, "fix-no-stories",
                       {"vehicle_model": "Hyundai Creta"})
    r = turn(pipe, "Bác có kỷ niệm nào đáng nhớ với xe không?",
             persona_id=pid)
    eid = r.saved_stories[0]["event_id"]
    st = session.get(PersonaStory, eid)
    st.status = "revoked"
    session.commit()
    r2 = turn(pipe, "Kể lại chuyến đi đó đi bác.", thread_id=r.thread_id,
              persona_id=pid)
    # Story đã thu hồi không được kể lại như canonical active.
    assert r2.status == "sent"
