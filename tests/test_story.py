"""Story: kể từ canon đã có, không tự tạo story trong lượt.

In-turn propose_story/propose_addendum đã bị gỡ (Phase 2c): persona
không tự thêm canon trong lúc trả lời. Story mới chỉ đi qua luồng
admin (find_gaps -> validate -> publish). Logic nhất quán story
(check_story/check_addendum) vẫn sống trong app.engine.consistency
và được dùng bởi persona_validator/enrichment.
"""
from sqlalchemy import select

from app.engine.consistency import check_addendum, check_story
from app.engine.tools import ToolExecutor
from app.models import PersonaStory, StoryTold
from conftest import turn


def test_create_and_recall_story(pipe, session):
    from conftest import make_persona
    # Persona chưa có story: trả lời "chưa nhớ ra", không bịa story mới.
    pid = make_persona(session, "fix-no-stories",
                       {"vehicle_model": "Hyundai Creta"})
    r = turn(pipe, "Bác có kỷ niệm nào đáng nhớ với xe không?",
             persona_id=pid)
    assert r.status == "sent"
    assert not r.saved_stories, "không được tạo story mới trong lượt"
    # Persona có story canon (AUTO_029): kể lại từ canon, không tạo mới.
    r1 = turn(pipe, "Bác có kỷ niệm nào đáng nhớ với xe không?")
    assert r1.status == "sent"
    assert not r1.saved_stories
    told = session.execute(
        select(StoryTold).where(StoryTold.thread_id == r1.thread_id,
                                 StoryTold.actor_id == "actor-1")
    ).scalars().all()
    assert len(told) >= 1, "story canon phải được kể và ghi StoryTold"
    r2 = turn(pipe, "Kể lại chuyến đi đó đi bác.", thread_id=r1.thread_id)
    assert r2.status == "sent"
    assert not r2.saved_stories


def test_same_story_rephrased_reuses_event_id(session):
    cand = PersonaStory(
        event_id="evt-nam-dinh", persona_id="hoang-nam",
        title="Chuyến về Nam Định", summary="Đi Creta về Nam Định cuối tuần",
        body="Cuối tuần mình lái Creta về Nam Định, đường êm, cả nhà thoải mái.",
        event_type="trip", status="active",
        entities={"vehicle": "Hyundai Creta", "places": ["Nam Định"]})
    session.add(cand)
    session.commit()
    verdict = check_story(
        {"title": "Về quê cuối tuần", "summary": "Lái Creta về Nam Định cuối tuần",
         "body": "Mình lái Creta về Nam Định cuối tuần, đường trường êm, nhà ngồi thoải mái.",
         "event_type": "trip",
         "entities": {"vehicle": "Hyundai Creta", "places": ["Nam Định"]}},
        [cand], purchased_at=None)
    assert verdict["verdict"] == "SAME_EVENT"
    assert verdict["event_id"] == "evt-nam-dinh"


def test_addendum_and_conflicting_addendum(session):
    story = PersonaStory(
        event_id="evt-que", persona_id="hoang-nam",
        title="Chuyến về quê", summary="Đi Creta về Nam Định",
        body="Mình lái Creta về Nam Định.",
        event_type="trip", status="active",
        entities={"vehicle": "Hyundai Creta", "places": ["Nam Định"]})
    session.add(story)
    session.commit()
    ok = check_addendum(story, "Hôm đó đi cùng cả nhà.",
                        {"persons": ["gia đình"]})
    assert ok["verdict"] == "COMPATIBLE"
    bad = check_addendum(
        story, "Thực ra không phải Nam Định mà là nơi khác.", {})
    assert bad["verdict"] == "CONFLICT"


def test_timeline_before_purchase_rejected(session):
    verdict = check_story(
        {"title": "Đi xe trước khi mua", "summary": "Đi Creta từ 2023",
         "body": "Mình đi Creta từ 2023.",
         "event_type": "trip", "event_date": "2023-06-01",
         "event_date_precision": "day",
         "entities": {"vehicle": "Hyundai Creta"}},
        [], purchased_at="2024-03")
    assert verdict["verdict"] == "CONFLICT"


def test_two_personas_isolated(session):
    from app.models import Persona
    session.add(Persona(id="persona-b", display_name="Người B",
                        region="Hà Nội", stance="peer_user"))
    story = PersonaStory(
        event_id="evt-a", persona_id="hoang-nam",
        title="Chuyến A", summary="Đi Creta về quê",
        body="Mình đi Creta về quê.", event_type="trip", status="active",
        entities={"vehicle": "Hyundai Creta", "places": ["Nam Định"]})
    session.add(story)
    session.commit()
    # Executor của persona B không thấy story của persona A.
    ex_b = ToolExecutor(session, "persona-b", "t", "actor-1")
    got = ex_b.execute("get_persona_stories", {"event_id": "evt-a"})
    assert got.status == "NOT_FOUND"


def test_revoke_story(pipe, session):
    # Thu hồi toàn bộ story canon của AUTO_029: không còn gì để kể lại.
    stories = session.execute(
        select(PersonaStory).where(PersonaStory.persona_id == "AUTO_029",
                                   PersonaStory.status == "active")
    ).scalars().all()
    assert stories
    for st in stories:
        st.status = "revoked"
    session.commit()
    r = turn(pipe, "Bác có kỷ niệm nào đáng nhớ với xe không?")
    assert r.status == "sent"
    told = session.execute(
        select(StoryTold).where(StoryTold.thread_id == r.thread_id)
    ).scalars().all()
    assert told == [], "story đã thu hồi không được kể lại"
