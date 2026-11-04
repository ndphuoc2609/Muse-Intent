"""Hoi quy cho 3 diem phat hien them tu live 5 kich ban 2026-11-04.

1. Validator chan echo mau khach neu: 3 luot review chi vi echo "trang"
   khach vua neu (S1 T2/T3, S2 T2). Mau co trong tin nhan khach vua gui
   thi tinh la echo, khong bat claim.
2. Bo sung dai ly theo tinh bo sot fact khu vuc luu dang
   predicate='location', relation=None (S3 T3 khong neu duoc dai ly HN).
   find_thread_area khop ca relation lan predicate.
3. Gia tri story de validator doi chieu bi nhan doi khi summary == body
   (mem_1) vi ghep summary + body + addenda.
"""
from app.engine.rag import find_thread_area
from app.engine.tools import ToolExecutor
from app.engine.validator import validate
from app.models import CustomerFact, PersonaStory


def _echo_final():
    return {"text": "Bản tiêu chuẩn màu trắng nghe hợp mắt đó em. "
                     "Em đang ở tỉnh hoặc khu vực nào vậy?",
             "goal": "explore_need", "claims": []}


def test_echo_color_from_user_message_passes():
    errors = validate(_echo_final(), {
        "retrieved": {},
        "user_text": "Em tính mua bản tiêu chuẩn màu trắng"})
    assert not any("màu xe 'trắng'" in e for e in errors)


def test_other_color_still_blocked():
    errors = validate(_echo_final(), {
        "retrieved": {},
        "user_text": "Em tính mua bản tiêu chuẩn"})
    assert any("màu xe 'trắng'" in e for e in errors)


def _location_fact(session, thread_id, **kw):
    row = CustomerFact(
        thread_id=thread_id, actor_id="actor-1", subject="self",
        predicate=kw.get("predicate", "location"),
        relation=kw.get("relation"),
        value_raw=kw.get("value_raw", "Hà Nội"),
        value_normalized=kw.get("value_normalized", "Hà Nội"),
        evidence="Mình ở Hà Nội", source="stated", status="current")
    session.add(row)
    session.commit()
    return row


def test_find_thread_area_matches_predicate_location(session):
    # Dung dang Luna da luu o S3 live: predicate='location', relation=None.
    _location_fact(session, "t-pred")
    assert find_thread_area(session, "t-pred", "actor-1") == "Hà Nội"


def test_find_thread_area_matches_relation_location(session):
    _location_fact(session, "t-rel", predicate="current_location",
                   relation="location", value_raw="Đà Nẵng",
                   value_normalized="Đà Nẵng")
    assert find_thread_area(session, "t-rel", "actor-1") == "Đà Nẵng"


def test_find_thread_area_empty_when_absent(session):
    assert find_thread_area(session, "t-none", "actor-1") == ""


def _story(session, persona_id, event_id, summary, body):
    from tests.conftest import make_persona
    make_persona(session, persona_id)
    row = PersonaStory(persona_id=persona_id, event_id=event_id,
                       title="", summary=summary, body=body,
                       event_type="memory", status="active")
    session.add(row)
    session.commit()
    return row


def test_story_retrieved_value_dedupes_identical_summary_body(session):
    _story(session, "fix-story-dedupe", "fix-story-dedupe_mem_1",
           "Thường chở 2 chú chó Golden đi dạo công viên cuối tuần",
           "Thường chở 2 chú chó Golden đi dạo công viên cuối tuần")
    ex = ToolExecutor(session, "fix-story-dedupe", "t", "actor-1")
    r = ex.execute("get_persona_stories", {})
    assert r.status == "FOUND"
    kind, value, _meta = ex.retrieved["fix-story-dedupe_mem_1"]
    assert kind == "story"
    assert value == ("Thường chở 2 chú chó Golden đi dạo công viên "
                     "cuối tuần")
    assert value.count("công viên") == 1


def test_story_retrieved_value_keeps_distinct_parts(session):
    _story(session, "fix-story-distinct", "fix-story-distinct_mem_1",
           "Đi Đà Lạt cuối tuần", "Đường đèo sương mù dày đặc")
    ex = ToolExecutor(session, "fix-story-distinct", "t", "actor-1")
    r = ex.execute("get_persona_stories", {})
    assert r.status == "FOUND"
    _kind, value, _meta = ex.retrieved["fix-story-distinct_mem_1"]
    assert value == "Đi Đà Lạt cuối tuần Đường đèo sương mù dày đặc"
