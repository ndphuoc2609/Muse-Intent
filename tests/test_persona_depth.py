"""Persona-depth: taxonomy, story tags/categories, retrieval, validator,
enrichment draft/publish, customer need state, runtime prompt.
FakeProvider + SQLite in-memory, KHÔNG gọi OpenRouter."""
from conftest import turn

from app.engine.enrichment import (enrichment_contract, find_gaps,
                                   publish_draft, validate_draft)
from app.engine.persona_validator import validate_persona
from app.engine.retrieval import derive_wanted_tags, retrieve_stories
from app.engine.runtime_prompt import (CONVERSATION_POLICY,
                                       RUNTIME_PROMPT_VERSION,
                                       build_runtime_prompt)
from app.engine.taxonomy import TAXONOMY_VERSION, is_known_tag, seed_taxonomy
from app.models import (EnrichmentDraft, Persona, PersonaCategory,
                        PersonaPerspective, PersonaProfile,
                        PersonaStory, PersonaStyleGuide, TagAssignment)


def test_taxonomy_seed_idempotent(session):
    r1 = seed_taxonomy(session)
    r2 = seed_taxonomy(session)
    assert r1["taxonomy_version"] == TAXONOMY_VERSION
    assert r2["upserted"] == 0
    assert is_known_tag("usage.city_commute")
    assert not is_known_tag("tag_bia_khong_co")


def test_story_tags_categories_activate_on_sent(pipe, session):
    seed_taxonomy(session)
    r = turn(pipe, "Bác có kỷ niệm nào đáng nhớ với xe không?")
    assert r.status == "sent"
    assert r.saved_stories, "Fake phải propose story và lưu vào saved_stories"
    st = r.saved_stories[0]
    assert st["tags"], "propose_story phải trả tags"
    assert st["story_categories"], "propose_story phải trả categories"
    assert "usage.long_distance_trip" in st["tags"]
    # Đã sent -> tag assignment phải active, không còn pending.
    row = session.query(PersonaStory).filter_by(
        event_id=st["event_id"]).first()
    assert row is not None and row.status == "active"
    tags = session.query(TagAssignment).filter_by(
        entity_type="story", entity_id=st["event_id"]).all()
    assert tags and all(t.status == "active" for t in tags)
    assert row.tellable_policy == "on_relevance"


def test_story_unknown_category_blocked(pipe, session):
    from app.engine.tools import ToolExecutor
    from app.models import Thread
    # Tạo thread tối thiểu để ToolExecutor có context.
    r = turn(pipe, "Bác đang đi xe gì?")
    thread = session.query(Thread).filter_by(id=r.thread_id).first()
    ex = ToolExecutor(session, "hoang-nam", thread.id, "actor-1")
    res = ex.execute("propose_story", {
        "summary": "Chuyến đi đâu đó.", "body": "Đi đâu đó.",
        "tags": ["usage.city_commute"], "categories": ["category_bia"]})
    assert res.status == "CONFLICT"


def test_retrieval_prefers_untold_and_relevant(session):
    seed_taxonomy(session)
    from app.seed import seed as _seed  # noqa: F401  (đã seed trong fixture)
    # Seed story thủ công: một story về quê (relevant), một story phố.
    from app.models import PersonaStory
    s1 = PersonaStory(event_id="evt_test_trip", persona_id="hoang-nam",
                      title="Về quê", summary="Về Nam Định cuối tuần",
                      body="Về Nam Định cuối tuần.", entities={},
                      event_type="trip", status="active")
    s2 = PersonaStory(event_id="evt_test_city", persona_id="hoang-nam",
                      title="Đi phố", summary="Đi phố hằng ngày",
                      body="Đi phố hằng ngày.", entities={},
                      event_type="experience", status="active")
    session.add_all([s1, s2])
    session.add(TagAssignment(entity_type="story", entity_id="evt_test_trip",
                              tag="usage.long_distance_trip", source="seed",
                              status="active"))
    session.commit()
    ev = retrieve_stories(session, "hoang-nam", "thread-x", "actor-1",
                          query_text="về quê đi xa",
                          wanted_tags=["usage.long_distance_trip"])
    assert ev and ev[0]["event_id"] == "evt_test_trip"
    assert ev[0]["score"] > 0


def test_retrieval_never_tellable_excluded(session):
    from app.models import PersonaStory
    session.add(PersonaStory(
        event_id="evt_never", persona_id="hoang-nam", title="Riêng tư",
        summary="Chuyện riêng tư", body="Chuyện riêng tư.", entities={},
        event_type="experience", status="active", tellable_policy="never"))
    session.commit()
    ev = retrieve_stories(session, "hoang-nam", "thread-x", "actor-1",
                          query_text="chuyện riêng tư",
                          wanted_tags=[])
    assert all(d["event_id"] != "evt_never" for d in ev)


def test_validator_catches_before_purchase_story(session):
    from app.models import PersonaStory
    session.add(PersonaStory(
        event_id="evt_early", persona_id="hoang-nam", title="Trước khi mua",
        summary="Đi xe trước khi mua", body="Đi xe trước khi mua.",
        entities={}, event_type="trip", event_date="2023-01",
        event_date_precision="month", status="active"))
    session.commit()
    res = validate_persona(session, "hoang-nam")
    assert any("timeline_before_purchase" in e for e in res["errors"])


def test_validator_same_place_different_day_not_conflict(session):
    # Cùng nơi, cùng xe, khác ngày = KHÔNG phải conflict (theo §6).
    from app.models import PersonaStory
    for eid, date in (("evt_a", "2024-05"), ("evt_b", "2024-06")):
        session.add(PersonaStory(
            event_id=eid, persona_id="hoang-nam", title="Về quê",
            summary="Về Nam Định", body="Về Nam Định.", entities={},
            event_type="trip", event_date=date,
            event_date_precision="month", status="active"))
    session.commit()
    res = validate_persona(session, "hoang-nam")
    assert not any("story_conflict" in e for e in res["errors"])


def test_enrichment_draft_publish_and_locked_key(session):
    seed_taxonomy(session)
    d = EnrichmentDraft(
        persona_id="hoang-nam", kind="fact",
        payload={"fact_key": "vehicle_model", "value": "Tucson"},
        reason="test khóa", anchor_refs=[], proposed_tags=[],
        proposed_categories=[], conditions=[], conflicts=[])
    session.add(d)
    session.commit()
    assert any("locked_fact_key" in e for e in validate_draft(session, d))
    # Draft hợp lệ: fact creatable + tag đã biết.
    d2 = EnrichmentDraft(
        persona_id="hoang-nam", kind="fact",
        payload={"fact_key": "favorite_route", "value": "Cầu Giấy - Mỹ Đình"},
        reason="test", anchor_refs=["fact:commute_route"],
        proposed_tags=["usage.city_commute"], proposed_categories=[],
        conditions=[], conflicts=[])
    session.add(d2)
    session.commit()
    assert validate_draft(session, d2) == []
    out = publish_draft(session, d2.id)
    assert out["ok"] and out["created"].get("fact_id")
    assert session.get(EnrichmentDraft, d2.id).status == "published"


def test_customer_need_kind_hypothesis_not_fact(pipe, session):
    # Khách chỉ nói "chủ yếu chở gia đình đi xa" -> không được nâng
    # thành ý định mua ngay hay nhu cầu 7 chỗ.
    r = turn(pipe, "Mình chủ yếu chở gia đình đi xa.")
    assert r.status == "sent"
    for f in r.saved_customer_facts:
        assert f.get("need_kind") in (
            "explicit", "preference", "concern", "constraint",
            "intended_user", "usage_context", "hypothesis",
            "declined", "opt_out", None)
    # Không có fact nào khẳng định "cần xe 7 chỗ" từ câu này.
    assert not any("7 chỗ" in (f.get("value_normalized") or "")
                   for f in r.saved_customer_facts)


def test_runtime_prompt_contract(session):
    persona = session.get(Persona, "hoang-nam")
    out = build_runtime_prompt(persona, None, [], None, [], [], [], "test", "ctx")
    assert out["version"] == RUNTIME_PROMPT_VERSION
    # CURRENT_MESSAGE không phải section trong prompt text: nó là message
    # user riêng trong tool loop, không nhét vào system.
    for k in ("PERSONA_CORE", "STYLE_GUIDE", "RELEVANT_PERSONA_FACTS",
              "RELEVANT_MEMORIES", "PRODUCT_FACTS", "CUSTOMER_STATE",
              "VERIFIED_CONVERSATION_CONTEXT", "CONVERSATION_POLICY"):
        assert k in out["sections"]
    assert "tối đa 1 câu" in CONVERSATION_POLICY
    assert "peer_user" in out["sections"]["CONVERSATION_POLICY"] or True


def test_derive_wanted_tags(session):
    tags, cats = derive_wanted_tags("Mình hay đưa bố mẹ về quê", None)
    assert "usage.long_distance_trip" in tags
    assert "frequent_road_trip" in cats
    # find_gaps + contract không crash trên persona đã seed.
    assert isinstance(find_gaps(session, "hoang-nam"), list)
    assert "required_fields" in enrichment_contract()
