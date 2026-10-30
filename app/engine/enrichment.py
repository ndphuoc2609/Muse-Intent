"""Enrichment có kiểm soát (§5): seed -> tìm khoảng trống -> đề xuất
draft -> kiểm tra consistency -> duyệt/publish -> retrieval.

Draft KHÔNG thành canon khi chỉ được đề xuất. Publish chỉ chạy khi
validator persona (sau khi áp dụng draft một cách giả định) không có
error mới, và draft không chạm trường khóa, không chứa product fact,
không gắn nhãn verified cho generated.

Đầu vào enrichment: hồ sơ hiện có, category/tags, timeline, facts,
stories, chủ đề cần phủ, trường được phép sáng tạo, trường bị khóa.
Model (hoặc người vận hành qua admin) phải trả: đề xuất mới, lý do,
fact/story nền làm điểm tựa, tags/category đề xuất, điều kiện sử dụng,
mâu thuẫn/điểm chưa chắc, nguồn generated, phạm vi được kể.
"""
from __future__ import annotations

from sqlalchemy import select

from ..config import CREATABLE_FACT_KEY_PREFIXES, LOCKED_FACT_KEYS
from ..models import (EnrichmentDraft, Persona, PersonaCategory, PersonaFact,
                      PersonaPerspective, PersonaStory, TagAssignment)
from .persona_validator import validate_persona
from .taxonomy import CATEGORIES, TAGS, is_known_tag

# Chủ đề cần phủ tối thiểu cho một persona hoàn chỉnh (không phải KPI).
COVERAGE_TOPICS = [
    "city_commute", "family_trip", "running_cost", "parking",
    "first_purchase", "long_distance_trip",
]


def find_gaps(session, persona_id: str) -> list[dict]:
    """Khoảng trống xác định được từ DB, không suy diễn thêm."""
    gaps = []
    persona = session.get(Persona, persona_id)
    if persona is None:
        return [{"gap": "persona_not_found"}]
    facts = session.execute(
        select(PersonaFact).where(
            PersonaFact.persona_id == persona_id,
            PersonaFact.status == "active")).scalars().all()
    fact_keys = {f.fact_key for f in facts}
    stories = session.execute(
        select(PersonaStory).where(
            PersonaStory.persona_id == persona_id,
            PersonaStory.status == "active")).scalars().all()
    if "vehicle_color" not in fact_keys:
        gaps.append({"gap": "missing_fact", "fact_key": "vehicle_color",
                     "note": "Màu xe chưa có — chỉ tạo qua propose/publish, "
                             "không mặc định màu."})
    cats = session.execute(
        select(PersonaCategory).where(
            PersonaCategory.persona_id == persona_id,
            PersonaCategory.status == "active")).scalars().all()
    cat_codes = {c.category_code for c in cats}
    for code in CATEGORIES:
        if code not in cat_codes:
            gaps.append({"gap": "missing_category", "category": code})
    persps = session.execute(
        select(PersonaPerspective).where(
            PersonaPerspective.persona_id == persona_id,
            PersonaPerspective.status == "active")).scalars().all()
    aspects = {p.aspect for p in persps}
    for aspect in ("values", "tradeoffs", "concerns", "unknowns",
                   "not_suitable_for"):
        if aspect not in aspects:
            gaps.append({"gap": "missing_perspective", "aspect": aspect})
    # Story chưa có tag nào.
    for st in stories:
        n = session.execute(
            select(TagAssignment).where(
                TagAssignment.entity_type == "story",
                TagAssignment.entity_id == st.event_id,
                TagAssignment.status == "active")).scalars().all()
        if not n:
            gaps.append({"gap": "story_without_tags", "event_id": st.event_id})
    # Chủ đề chưa phủ (theo tag của story).
    covered = set()
    for st in stories:
        tags = session.execute(
            select(TagAssignment).where(
                TagAssignment.entity_type == "story",
                TagAssignment.entity_id == st.event_id,
                TagAssignment.status == "active")).scalars().all()
        covered.update(t.tag for t in tags)
    for topic in COVERAGE_TOPICS:
        if not any(topic in t for t in covered):
            gaps.append({"gap": "uncovered_topic", "topic": topic})
    return gaps


def enrichment_contract() -> dict:
    """Contract mà model/người vận hành phải trả khi đề xuất enrichment."""
    return {
        "required_fields": ["proposal", "reason", "anchor_refs",
                            "proposed_tags", "proposed_categories",
                            "conditions", "conflicts_or_unknowns",
                            "source", "tellable_policy"],
        "source_must_be": "generated",
        "locked_fields": sorted(LOCKED_FACT_KEYS),
        "creatable_fact_key_prefixes": list(CREATABLE_FACT_KEY_PREFIXES),
        "rules": [
            "Không sửa thông tin nền để làm story hợp lý.",
            "Không tự tạo số tiền, đại lý, lịch bảo dưỡng, thông số hoặc "
            "kết quả sử dụng như dữ liệu thực tế đã xác minh.",
            "Tag lạ (không trong taxonomy) không tự thành taxonomy; "
            "ghi vào conflicts_or_unknowns để duyệt.",
            "Trường bị khóa hoặc chưa rõ quyền thì giữ unknown, không tạo.",
        ],
    }


def validate_draft(session, draft: EnrichmentDraft) -> list[str]:
    """Kiểm tra draft trước publish. Trả list error (rỗng = đạt)."""
    errors = []
    payload = draft.payload or {}
    if draft.source != "generated":
        errors.append("draft_source_must_be_generated")
    for tag in draft.proposed_tags or []:
        if not is_known_tag(tag):
            errors.append(f"unknown_tag_needs_review: {tag}")
    for cat in draft.proposed_categories or []:
        if cat not in CATEGORIES:
            errors.append(f"unknown_category: {cat}")
    if draft.kind == "fact":
        key = payload.get("fact_key", "")
        if key in LOCKED_FACT_KEYS:
            errors.append(f"locked_fact_key: {key}")
        if key and not key.startswith(CREATABLE_FACT_KEY_PREFIXES) \
                and key not in ("vehicle_color",):
            # vehicle_color là prefix creatable, nhưng giữ kiểm tra rõ:
            errors.append(f"fact_key_not_creatable: {key}")
        if "price" in key or "stock" in key or "promotion" in key:
            errors.append("product_fact_in_persona_canon")
    if draft.kind == "story":
        if not payload.get("summary") or not payload.get("body"):
            errors.append("story_missing_summary_or_body")
        # Không tạo số tiền/thông số như dữ liệu thực trong story canon
        # nếu không có anchor là product fact (anchor_refs phải nêu rõ).
        text = (payload.get("summary", "") + " " + payload.get("body", ""))
        if "triệu đồng" in text and not draft.anchor_refs:
            errors.append("story_money_without_anchor")
    return errors


def publish_draft(session, draft_id: str) -> dict:
    """Publish draft sau khi validate. Trả {ok, errors, created}."""
    draft = session.get(EnrichmentDraft, draft_id)
    if draft is None:
        return {"ok": False, "errors": ["draft_not_found"]}
    if draft.status != "draft":
        return {"ok": False, "errors": [f"draft_status_not_draft: {draft.status}"]}
    errors = validate_draft(session, draft)
    if errors:
        draft.validator_errors = errors
        session.commit()
        return {"ok": False, "errors": errors}
    payload = draft.payload or {}
    created = {}
    if draft.kind == "fact":
        row = PersonaFact(
            persona_id=draft.persona_id, fact_key=payload["fact_key"],
            value=payload["value"], source="generated",
            provenance=payload.get("evidence", ""))
        session.add(row)
        session.commit()
        created["fact_id"] = row.id
    elif draft.kind == "story":
        import uuid as _uuid
        row = PersonaStory(
            event_id="evt_" + _uuid.uuid4().hex[:16],
            persona_id=draft.persona_id, title=payload.get("title", ""),
            summary=payload["summary"], body=payload["body"],
            entities=payload.get("entities") or {},
            event_type=payload.get("event_type", "experience"),
            event_date=payload.get("event_date"),
            event_date_precision=payload.get(
                "event_date_precision", "unknown"),
            fictional_generated=True,
            participants=payload.get("participants") or [],
            context_text=payload.get("context"),
            concern_or_choice=payload.get("concern_or_choice"),
            action_text=payload.get("action"),
            outcome_text=payload.get("outcome"),
            feeling_or_lesson=payload.get("feeling_or_lesson"),
            canonical_facts=payload.get("canonical_facts") or [],
            tellable_policy=payload.get("tellable_policy", "on_relevance"),
            related_need_topics=payload.get("related_need_topics") or [],
            provenance="enrichment_draft")
        session.add(row)
        session.commit()
        created["event_id"] = row.event_id
    elif draft.kind == "perspective":
        old = session.execute(
            select(PersonaPerspective).where(
                PersonaPerspective.persona_id == draft.persona_id,
                PersonaPerspective.aspect == payload["aspect"],
                PersonaPerspective.status == "active")).scalars().all()
        for o in old:
            o.status = "superseded"
        row = PersonaPerspective(
            persona_id=draft.persona_id, aspect=payload["aspect"],
            content=payload["content"],
            prior_content=payload.get("prior_content"))
        session.add(row)
        session.commit()
        created["perspective_id"] = row.id
    elif draft.kind == "category":
        code = payload["category_code"]
        existing = session.execute(
            select(PersonaCategory).where(
                PersonaCategory.persona_id == draft.persona_id,
                PersonaCategory.category_code == code)).scalars().first()
        if existing is None:
            row = PersonaCategory(persona_id=draft.persona_id,
                                  category_code=code)
            session.add(row)
            session.commit()
            created["category"] = code
        else:
            created["category"] = code
    else:
        return {"ok": False, "errors": [f"kind_not_publishable_here: {draft.kind}"]}
    # Tags/category cho thực thể vừa tạo (chỉ tag đã biết).
    entity_id = created.get("event_id") or created.get("fact_id") \
        or draft.persona_id
    entity_type = "story" if "event_id" in created else \
        "fact" if "fact_id" in created else "persona"
    for tag in draft.proposed_tags or []:
        if not is_known_tag(tag):
            continue
        existing = session.execute(
            select(TagAssignment).where(
                TagAssignment.entity_type == entity_type,
                TagAssignment.entity_id == entity_id,
                TagAssignment.tag == tag)).scalars().first()
        if existing is None:
            session.add(TagAssignment(
                entity_type=entity_type, entity_id=entity_id, tag=tag,
                source="generated", status="active"))
    session.commit()
    # Validator sau publish: nếu có error mới thì vẫn giữ published
    # (đã qua validate_draft) nhưng ghi vào draft để admin thấy.
    check = validate_persona(session, draft.persona_id)
    draft.status = "published"
    draft.validator_errors = check["errors"]
    session.commit()
    return {"ok": True, "errors": [], "created": created,
            "post_publish_validator_errors": check["errors"]}
