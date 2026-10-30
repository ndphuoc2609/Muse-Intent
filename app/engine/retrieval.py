"""Retrieval theo ngữ cảnh (§7): exact/tag trước, không đổ toàn bộ
persona vào prompt, không ưu tiên story chỉ vì cùng tên xe.

Pipeline:
1. Exact facts khi câu hỏi cụ thể (keys đã có trong planner/data_needs
   được xử lý bằng tool get_persona_facts, module này lo phần story).
2. Story/memory theo category, tags, entities, thời gian, relevance.
3. Lọc tellable_policy=never, status active (pending không phải canon),
   và mâu thuẫn đã biết (story bị superseded qua StoryLink thì không
   trả về bản cũ khi đã có bản thay thế active).
4. Ưu tiên nội dung chưa kể cho actor này trong thread này, rồi mới
   đến đã kể (retell chỉ khi khách hỏi lại rõ ràng).
5. Trả tập bằng chứng nhỏ (mặc định tối đa 3 story) kèm lý do + điểm,
   để model quyết định kể hay chỉ trả lời trực tiếp.

Không dùng vector/semantic retrieval ở đây: exact + tag + entity đã đủ
cho quy mô hiện tại, và tránh phụ thuộc mới không có bằng chứng cần.
"""
from __future__ import annotations

from sqlalchemy import select

from ..models import (PersonaStory, StoryLink, StoryTold, TagAssignment)


def _tokens(text: str) -> set[str]:
    import re
    return set(re.findall(r"\w+", (text or "").lower()))


def _story_tags(session, event_id: str) -> list[str]:
    rows = session.execute(
        select(TagAssignment).where(
            TagAssignment.entity_type == "story",
            TagAssignment.entity_id == event_id,
            TagAssignment.status == "active")).scalars().all()
    return [r.tag for r in rows]


def _superseded_ids(session, persona_id: str) -> set[str]:
    """Các event_id cũ đã bị supersedes bởi một story active khác."""
    rows = session.execute(
        select(StoryLink).where(StoryLink.link_type == "supersedes")
    ).scalars().all()
    out = set()
    for link in rows:
        new = session.get(PersonaStory, link.to_event_id)
        if new is not None and new.persona_id == persona_id \
                and new.status == "active":
            out.add(link.from_event_id)
    return out


def retrieve_stories(session, persona_id: str, thread_id: str, actor_id: str,
                     query_text: str = "", wanted_tags: list[str] | None = None,
                     wanted_categories: list[str] | None = None,
                     retell_requested: bool = False,
                     limit: int = 3) -> list[dict]:
    """Trả list evidence dict, đã sắp xếp theo điểm giảm dần."""
    wanted_tags = wanted_tags or []
    wanted_categories = wanted_categories or []
    q_tokens = _tokens(query_text)
    stories = session.execute(
        select(PersonaStory).where(
            PersonaStory.persona_id == persona_id,
            PersonaStory.status == "active")).scalars().all()
    superseded = _superseded_ids(session, persona_id)
    told = set()
    if thread_id and actor_id:
        told_rows = session.execute(
            select(StoryTold).where(
                StoryTold.persona_id == persona_id,
                StoryTold.thread_id == thread_id,
                StoryTold.actor_id == actor_id)).scalars().all()
        told = {r.event_id for r in told_rows}
    scored = []
    for st in stories:
        if st.event_id in superseded:
            continue
        if (st.tellable_policy or "on_relevance") == "never":
            continue
        tags = _story_tags(session, st.event_id)
        score = 0.0
        reasons = []
        tag_overlap = set(tags) & set(wanted_tags)
        if tag_overlap:
            score += 3.0 * len(tag_overlap)
            reasons.append(f"tag trùng: {sorted(tag_overlap)}")
        # Category: story tag có namespace conversation_topic hoặc tag
        # chứa category? Category của story được lưu qua tag_assignments
        # với tag dạng category:<code> khi publish (xem enrichment).
        cat_tags = [t for t in tags if t.startswith("category:")]
        cat_codes = [t.split(":", 1)[1] for t in cat_tags]
        cat_overlap = set(cat_codes) & set(wanted_categories)
        if cat_overlap:
            score += 2.0 * len(cat_overlap)
            reasons.append(f"category trùng: {sorted(cat_overlap)}")
        entities = st.entities or {}
        ent_tokens = set()
        for v in entities.values():
            if isinstance(v, str):
                ent_tokens |= _tokens(v)
            elif isinstance(v, list):
                for item in v:
                    ent_tokens |= _tokens(str(item))
        if q_tokens & ent_tokens:
            score += 1.5
            reasons.append("entity trùng với câu hỏi")
        body_tokens = _tokens(st.summary + " " + st.body)
        if q_tokens & body_tokens:
            score += 0.5
            reasons.append("từ khóa trùng với nội dung story")
        # Ưu tiên chưa kể; đã kể chỉ lên cao khi khách hỏi lại rõ ràng.
        if st.event_id in told:
            if retell_requested:
                score += 1.0
                reasons.append("đã kể nhưng khách hỏi lại")
            else:
                score -= 2.0
                reasons.append("đã kể cho actor này (giảm ưu tiên)")
        # Không ưu tiên chỉ vì cùng tên xe: vehicle trùng mà không có
        # tag/entity/topic trùng thì không cộng điểm.
        scored.append({
            "event_id": st.event_id, "title": st.title,
            "summary": st.summary, "body": st.body,
            "event_type": st.event_type, "event_date": st.event_date,
            "event_date_precision": st.event_date_precision,
            "tags": tags, "entities": entities,
            "tellable_policy": st.tellable_policy or "on_relevance",
            "told_before": st.event_id in told,
            "score": round(score, 2), "reasons": reasons,
        })
    scored.sort(key=lambda d: (-d["score"], d["event_id"]))
    # Chỉ trả story có điểm > 0, trừ khi không có gì và cần fallback:
    # nếu tất cả điểm <= 0 thì trả rỗng (model trả lời trực tiếp, không
    # bị ép kể một story không liên quan).
    out = [d for d in scored if d["score"] > 0][:limit]
    for i, d in enumerate(out):
        d["rank"] = i + 1
    return out


def log_retrieval(session, persona_id: str, thread_id: str, actor_id: str,
                  evidence: list[dict]) -> None:
    from ..models import RetrievalLog
    for d in evidence:
        session.add(RetrievalLog(
            persona_id=persona_id, thread_id=thread_id, actor_id=actor_id,
            entity_type="story", entity_id=d["event_id"],
            reason="; ".join(d["reasons"]), score=d["score"],
            rank=d.get("rank", 0)))
    session.commit()


def derive_wanted_tags(query_text: str, planner) -> tuple[list[str], list[str]]:
    """Suy ra tag/category muốn truy xuất từ câu hỏi + planner.

    Đây là heuristic cho retrieval, KHÔNG phải quyết định nhu cầu của
    khách: model vẫn quyết định có kể hay không, và customer fact chỉ
    được tạo khi có evidence. Không suy diễn thuộc tính nhạy cảm.
    """
    text = (query_text or "").lower()
    intents_text = " ".join(
        (i.normalized or "") for i in (planner.intents or [])) if planner else ""
    hay = text + " " + intents_text.lower()
    tags: list[str] = []
    cats: list[str] = []
    if any(k in hay for k in ("về quê", "đi xa", "đường dài", "du lịch")):
        tags += ["usage.long_distance_trip", "experience.long_distance_trip"]
        cats.append("frequent_road_trip")
    if any(k in hay for k in ("gia đình", "bố mẹ", "cả nhà")):
        tags += ["usage.family_weekend", "lifestyle.family_with_parents"]
        cats.append("family_daily_use")
    if any(k in hay for k in ("đỗ", "xoay xở", "đi phố", "chung cư")):
        tags += ["usage.city_commute", "priority.parking_convenience",
                 "experience.city_parking"]
        cats.append("urban_commuter")
    if any(k in hay for k in ("tiết kiệm", "chi phí", "xăng", "bảo dưỡng")):
        tags += ["priority.running_cost", "concern.maintenance_cost"]
        cats.append("ownership_cost_focused")
    if any(k in hay for k in ("kỷ niệm", "chuyến", "nhớ")):
        tags += ["experience.long_distance_trip", "usage.family_weekend"]
    if any(k in hay for k in ("mua xe lần đầu", "xe đầu", "lần đầu")):
        tags += ["experience.first_purchase", "concern.first_car_uncertainty"]
        cats.append("first_car_owner")
    # Khử trùng, giữ thứ tự.
    return list(dict.fromkeys(tags)), list(dict.fromkeys(cats))
