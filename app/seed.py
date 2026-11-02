"""Seed persona từ dữ liệu import (data/personas.json, 200 persona).

Nguồn: endpoint /api/personas do Phước cung cấp (payload đã vendor vào
data/personas.json để seed không phụ thuộc endpoint lúc chạy).
Seed cũ (hoang-nam, minh-anh) đã bị THAY HẲN theo yêu cầu 2026-11-02:
seed() giờ nạp 200 persona mới và purge toàn bộ canon + thread của
persona nào không còn trong dữ liệu mới (kể cả hoang-nam/minh-anh).
Bản cũ vẫn khôi phục được từ git (nhánh dialogue-naturalness).

Mapping — chỉ map những gì nguồn có, KHÔNG bịa field nguồn không có:
- Persona: id=code (AUTO_xxx, unique), display_name=name,
  style=tone, stance=peer_user (mọi persona đều là user bình thường,
  không role), age=None, region="" (nguồn không có tuổi/vùng).
  is_fictional=True + identity_note hư cấu: GIẢ ĐỊNH từ mã AUTO_ và
  tên stylized (vd "Khang Vibe"); Phước chưa xác nhận fictional —
  nếu dữ liệu là người thật thì phải sửa chỗ này trước khi dùng rộng.
- PersonaProfile: occupation, family_structure=lifestyle.family_status,
  daily_rhythm=lifestyle.daily_commute,
  ownership_status="đang sở hữu" (suy ra từ chính tên field car_owned).
- PersonaFact (source="import"): duy nhất vehicle_model=car_owned
  NGUYÊN VĂN (không tách trim/màu từ text tự do, không bịa
  purchased_at/is_first_car vì nguồn không có).
- PersonaPerspective: aspect="values", content=perspective.
- PersonaStyleGuide: address_form="mình/bạn",
  style_examples=[sample_quote],
  avoid_patterns=["rao bán", "hứa hẹn giao dịch", "xin số điện thoại"]
  (policy hệ thống, giống seed cũ). Các field style khác NULL.
- PersonaStory: mỗi memory[] thành một story event_type="memory",
  event_id={code}_mem_{i}, title=cắt 60 ký tự đầu của text (suy ra
  cơ học, không bịa), summary=body=text nguyên văn,
  canonical_facts=[text], tellable_policy="on_relevance",
  fictional_generated=True, provenance="import", event_date=None.
  Story không có tag taxonomy (hashtag nguồn không thuộc taxonomy và
  TagAssignment.tag FK tới tag_definitions) nên retrieval chấm điểm
  qua entity/body keyword overlap, không qua tag/category.
- KHÔNG map vào bảng quan hệ (giữ nguyên văn trong data/personas.json
  để bước RAG dùng sau): role (không đưa role vào context — persona
  là peer_user, không role), personality_traits, hashtags, post_history,
  lifestyle.hobbies, lifestyle.practical_painpoints,
  lifestyle.multi_angle_ready, category 5 nhóm của nguồn (không phải
  mã taxonomy 7 nhóm hiện tại).

Idempotent: persona/profile/style upsert theo id (ghi đè từ nguồn);
fact/perspective/story chỉ thêm khi chưa có, không tạo trùng khi
gọi /seed nhiều lần. Product facts mẫu (Creta/Tucson) và actors giữ
nguyên — yêu cầu thay chỉ áp dụng cho dữ liệu persona.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import delete, select

from .engine.taxonomy import seed_taxonomy
from .models import (
    Actor, CustomerFact, Delivery, EnrichmentDraft, Message,
    Persona, PersonaCategory, PersonaFact, PersonaPerspective,
    PersonaProfile, PersonaStory, PersonaStyleGuide, ProductFact,
    QuestionState, RetrievalLog, StoryLink, StoryRevision, StoryTold,
    Thread, TurnIdempotency, TagAssignment,
)

_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "personas.json"
_AVOID = ["rao bán", "hứa hẹn giao dịch", "xin số điện thoại"]
_FICTIONAL_NOTE = "PERSONA HƯ CẤU cho sandbox, không phải người thật."


def _load_source() -> list[dict]:
    with open(_DATA_PATH, encoding="utf-8") as f:
        payload = json.load(f)
    personas = payload.get("personas") or []
    if not personas:
        raise ValueError(f"{_DATA_PATH} không chứa persona nào")
    for p in personas:
        if not p.get("code") or not p.get("name") or not p.get("car_owned"):
            raise ValueError(f"persona thiếu code/name/car_owned: {p}")
    codes = [p["code"] for p in personas]
    if len(set(codes)) != len(codes):
        raise ValueError("code persona trùng nhau trong dữ liệu nguồn")
    return personas


def _fact(session, persona_id, key, value, src="import"):
    row = session.execute(
        select(PersonaFact).where(
            PersonaFact.persona_id == persona_id,
            PersonaFact.fact_key == key,
            PersonaFact.status == "active")).scalars().first()
    if row is None:
        session.add(PersonaFact(persona_id=persona_id, fact_key=key,
                                value=value, source=src))
        return True
    return False


def _perspective(session, persona_id, aspect, content):
    row = session.execute(
        select(PersonaPerspective).where(
            PersonaPerspective.persona_id == persona_id,
            PersonaPerspective.aspect == aspect,
            PersonaPerspective.status == "active")).scalars().first()
    if row is None:
        session.add(PersonaPerspective(
            persona_id=persona_id, aspect=aspect, content=content))
        return True
    return False


def _ensure_persona(session, p: dict) -> None:
    code = p["code"]
    lc = p.get("lifestyle_context") or {}
    persona = session.get(Persona, code)
    if persona is None:
        session.add(Persona(
            id=code, display_name=p["name"], age=None, region="",
            stance="peer_user", style=p.get("tone") or "",
            identity_note=_FICTIONAL_NOTE, is_fictional=True))
    else:
        persona.display_name = p["name"]
        persona.style = p.get("tone") or ""
        persona.stance = "peer_user"
        persona.identity_note = _FICTIONAL_NOTE
        persona.is_fictional = True
    profile = session.get(PersonaProfile, code)
    if profile is None:
        session.add(PersonaProfile(
            persona_id=code, occupation=p.get("occupation"),
            daily_rhythm=lc.get("daily_commute"),
            family_structure=lc.get("family_status"),
            ownership_status="đang sở hữu",
            purchase_context=None, spending_limit_note=None))
    else:
        profile.occupation = p.get("occupation")
        profile.daily_rhythm = lc.get("daily_commute")
        profile.family_structure = lc.get("family_status")
        profile.ownership_status = "đang sở hữu"
    style = session.get(PersonaStyleGuide, code)
    examples = [p["sample_quote"]] if p.get("sample_quote") else []
    if style is None:
        session.add(PersonaStyleGuide(
            persona_id=code, address_form="mình/bạn",
            answer_length=None, intimacy=None, emoji_use=None,
            storytelling=None, proactive_question_level=None,
            avoid_patterns=list(_AVOID), style_examples=examples))
    else:
        style.address_form = "mình/bạn"
        style.avoid_patterns = list(_AVOID)
        style.style_examples = examples
    _fact(session, code, "vehicle_model", p["car_owned"])
    if p.get("perspective"):
        _perspective(session, code, "values", p["perspective"])
    for i, text in enumerate(p.get("memory") or [], start=1):
        if not text or not str(text).strip():
            continue
        text = str(text)
        event_id = f"{code}_mem_{i}"
        if session.get(PersonaStory, event_id) is None:
            title = text if len(text) <= 60 else text[:57] + "..."
            session.add(PersonaStory(
                event_id=event_id, persona_id=code, title=title,
                summary=text, body=text, entities={},
                event_type="memory", event_date=None,
                event_date_precision="unknown", fictional_generated=True,
                status="active", provenance="import",
                participants=[], context_text=None, concern_or_choice=None,
                action_text=None, outcome_text=None, feeling_or_lesson=None,
                canonical_facts=[text], tellable_policy="on_relevance",
                related_need_topics=[], story_categories=[]))


def _purge_removed(session, keep_ids: set[str]) -> list[str]:
    """Xóa hẳn persona không còn trong dữ liệu mới + toàn bộ dữ liệu
    gắn với chúng (canon, thread, message, customer fact...). Chỉ chạy
    khi seed() được gọi; DB mới không có gì để xóa nên là no-op."""
    old = session.execute(
        select(Persona).where(Persona.id.notin_(keep_ids))).scalars().all()
    removed = []
    for persona in old:
        pid = persona.id
        thread_ids = [t.id for t in session.execute(
            select(Thread).where(Thread.persona_id == pid)).scalars().all()]
        msg_ids = []
        if thread_ids:
            msg_ids = [m.id for m in session.execute(
                select(Message).where(
                    Message.thread_id.in_(thread_ids))).scalars().all()]
        story_ids = [st.event_id for st in session.execute(
            select(PersonaStory).where(
                PersonaStory.persona_id == pid)).scalars().all()]
        fact_ids = [f.id for f in session.execute(
            select(PersonaFact).where(
                PersonaFact.persona_id == pid)).scalars().all()]
        if msg_ids:
            session.execute(delete(Delivery).where(
                Delivery.message_id.in_(msg_ids)))
            session.execute(delete(StoryTold).where(
                StoryTold.message_id.in_(msg_ids)))
        if thread_ids:
            session.execute(delete(TurnIdempotency).where(
                TurnIdempotency.thread_id.in_(thread_ids)))
            session.execute(delete(QuestionState).where(
                QuestionState.thread_id.in_(thread_ids)))
            session.execute(delete(CustomerFact).where(
                CustomerFact.thread_id.in_(thread_ids)))
            session.execute(delete(Message).where(
                Message.thread_id.in_(thread_ids)))
            session.execute(delete(Thread).where(
                Thread.persona_id == pid))
        session.execute(delete(StoryTold).where(
            StoryTold.persona_id == pid))
        if story_ids:
            session.execute(delete(StoryLink).where(
                StoryLink.from_event_id.in_(story_ids)))
            session.execute(delete(StoryLink).where(
                StoryLink.to_event_id.in_(story_ids)))
            session.execute(delete(StoryRevision).where(
                StoryRevision.event_id.in_(story_ids)))
            session.execute(delete(TagAssignment).where(
                TagAssignment.entity_type == "story").where(
                TagAssignment.entity_id.in_(story_ids)))
        session.execute(delete(TagAssignment).where(
            TagAssignment.entity_type == "persona").where(
            TagAssignment.entity_id == pid))
        if fact_ids:
            session.execute(delete(TagAssignment).where(
                TagAssignment.entity_type == "fact").where(
                TagAssignment.entity_id.in_(fact_ids)))
        session.execute(delete(RetrievalLog).where(
            RetrievalLog.persona_id == pid))
        session.execute(delete(EnrichmentDraft).where(
            EnrichmentDraft.persona_id == pid))
        session.execute(delete(PersonaStory).where(
            PersonaStory.persona_id == pid))
        session.execute(delete(PersonaFact).where(
            PersonaFact.persona_id == pid))
        session.execute(delete(PersonaPerspective).where(
            PersonaPerspective.persona_id == pid))
        session.execute(delete(PersonaProfile).where(
            PersonaProfile.persona_id == pid))
        session.execute(delete(PersonaStyleGuide).where(
            PersonaStyleGuide.persona_id == pid))
        session.execute(delete(PersonaCategory).where(
            PersonaCategory.persona_id == pid))
        session.delete(persona)
        removed.append(pid)
    return removed


def seed(session) -> dict:
    seed_taxonomy(session)
    personas = _load_source()
    for p in personas:
        _ensure_persona(session, p)
    removed = _purge_removed(session, {p["code"] for p in personas})
    # Product facts mẫu (miền riêng, giữ nguyên) + actors (giữ nguyên).
    exists = session.execute(
        select(ProductFact).where(
            ProductFact.model == "Hyundai Creta",
            ProductFact.fact_type == "price")).scalars().first()
    if exists is None:
        session.add(ProductFact(
            model="Hyundai Creta", trim="1.5 Cao cấp", fact_type="price",
            value_text="599 triệu đồng (giá mẫu, chưa phải giá thực tế)",
            value_number=599, unit="triệu đồng", location="Hà Nội",
            source="sample", valid_from="2026-01-01",
            valid_to="2026-12-31", is_sample=True))
        session.add(ProductFact(
            model="Hyundai Creta", trim="1.5 Cao cấp", fact_type="warranty",
            value_text="5 năm hoặc 100.000 km (dữ liệu mẫu)",
            source="sample", valid_from="2026-01-01",
            valid_to="2026-12-31", is_sample=True))
    exists = session.execute(
        select(ProductFact).where(
            ProductFact.model == "Hyundai Tucson",
            ProductFact.fact_type == "price")).scalars().first()
    if exists is None:
        session.add(ProductFact(
            model="Hyundai Tucson", trim="2.0 Đặc biệt", fact_type="price",
            value_text="769 triệu đồng (giá mẫu, chưa phải giá thực tế)",
            value_number=769, unit="triệu đồng", location="TP.HCM",
            source="sample", valid_from="2026-01-01",
            valid_to="2026-12-31", is_sample=True))
    for actor_id, name in (("actor-1", "Khách 1"), ("actor-2", "Khách 2")):
        if session.get(Actor, actor_id) is None:
            session.add(Actor(id=actor_id, display_name=name))
    session.commit()
    return {"seeded": True,
            "personas": [p["code"] for p in personas],
            "removed": removed}
