"""Validator persona (§6): kiểm tra canon, không chặn quan điểm thay đổi
theo thời gian hay hai sự kiện tương tự khác nhau.

Trả về dict {errors, warnings, unknowns}:
- errors: mâu thuẫn thật, sai timeline, nhầm persona, product fact lẫn
  vào canon, generated gắn nhãn verified, story never đã bị kể.
- warnings: thiếu thông tin có thể bổ sung, hoặc nghi ngờ cần người xem.
- unknowns: chưa đủ thông tin để kết luận (không tự kết luận mâu thuẫn).

Không dùng độ giống từ để kết luận hai chuyến đi cùng nơi, cùng xe
nhưng khác ngày là mâu thuẫn: chỉ xung đột khi cùng loại + cùng nơi +
cùng xe + ngày đủ chính xác mà khác nhau (xem consistency.check_story).
"""
from __future__ import annotations

import re

from sqlalchemy import select

from ..models import (Persona, PersonaFact, PersonaStory, StoryTold,
                      TagAssignment)
from .consistency import check_story

PRODUCT_FACT_KEYS = {
    "price", "stock", "inventory", "promotion", "discount", "warranty",
    "spec", "specification", "dealer_price", "offer",
}


def _fact_map(session, persona_id: str) -> dict[str, PersonaFact]:
    rows = session.execute(
        select(PersonaFact).where(
            PersonaFact.persona_id == persona_id,
            PersonaFact.status == "active")).scalars().all()
    return {r.fact_key: r for r in rows}


def _parse_year_month(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    m = re.match(r"(\d{4})-(\d{1,2})", value)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"(\d{4})", value)
    if m:
        return int(m.group(1)), 1
    return None


def validate_persona(session, persona_id: str) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    unknowns: list[str] = []
    persona = session.get(Persona, persona_id)
    if persona is None:
        return {"errors": ["persona_not_found"], "warnings": [],
                "unknowns": []}
    if persona.stance != "peer_user":
        errors.append(f"stance_not_peer_user: {persona.stance}")
    facts = _fact_map(session, persona_id)
    # 1. Tuổi / timeline / số năm lái xe.
    age = persona.age
    purchased = _parse_year_month(
        facts["purchased_at"].value if "purchased_at" in facts else None)
    if age is not None and purchased is not None:
        # Giả định năm hiện tại từ purchased? Không: tuổi hiện tại không
        # đủ để suy tuổi lúc mua nếu không biết năm hiện tại của canon.
        # Chỉ kiểm tra điều kiện tối thiểu: mua xe thì phải đủ 18 tuổi
        # tại thời điểm mua nếu biết năm sinh — năm sinh không có trong
        # schema, nên đây là unknown, không phải error.
        unknowns.append("age_at_purchase: cần birth_year để kiểm tra đủ 18 tuổi lúc mua")
    if "driving_years" in facts:
        try:
            dy = float(facts["driving_years"].value)
            if age is not None and dy > max(0, age - 18):
                errors.append(
                    f"driving_years_unrealistic: {dy} năm lái xe với tuổi {age}")
        except (TypeError, ValueError):
            warnings.append("driving_years_not_numeric")
    # 2. Mua xe trước trải nghiệm sở hữu tương ứng.
    purchased_str = facts["purchased_at"].value if "purchased_at" in facts else None
    stories = session.execute(
        select(PersonaStory).where(
            PersonaStory.persona_id == persona_id,
            PersonaStory.status == "active")).scalars().all()
    for st in stories:
        if st.persona_id != persona_id:
            errors.append(f"story_persona_mismatch: {st.event_id}")
        if st.event_type in ("ownership", "trip") and st.event_date \
                and purchased_str and st.event_date[:7] < purchased_str[:7]:
            errors.append(
                f"timeline_before_purchase: story {st.event_id} "
                f"{st.event_date} trước purchased_at {purchased_str}")
    # 3. Story trùng / mâu thuẫn (dùng consistency, không dùng giống từ).
    check = []
    for st in stories:
        others = [o for o in stories if o.event_id != st.event_id]
        verdict = check_story(
            {"summary": st.summary, "body": st.body,
             "entities": st.entities or {}, "event_type": st.event_type,
             "event_date": st.event_date,
             "event_date_precision": st.event_date_precision},
            others, purchased_str)
        if verdict["verdict"] == "CONFLICT":
            errors.append(f"story_conflict: {st.event_id}: {verdict['reason']}")
        elif verdict["verdict"] == "AMBIGUOUS":
            unknowns.append(f"story_ambiguous: {st.event_id}: {verdict['reason']}")
    # 4. Xe hiện tại vs xe đã bán: nếu có sold fact thì sold_at phải
    # sau purchased_at (nếu cả hai đều biết).
    if "vehicle_sold_at" in facts and purchased_str:
        sold = _parse_year_month(facts["vehicle_sold_at"].value)
        if sold and purchased and sold < purchased:
            errors.append("vehicle_sold_before_purchase")
    # 5. Product fact lẫn vào persona canon.
    for key, row in facts.items():
        base = key.split(".")[0] if "." in key else key
        if base in PRODUCT_FACT_KEYS or key.startswith("product_"):
            errors.append(f"product_fact_in_persona_canon: {key}")
        if row.source == "generated" and "verified" in (row.provenance or "").lower():
            errors.append(f"generated_labelled_verified: {key}")
    # 6. Story tellable=never đã bị kể.
    told = session.execute(
        select(StoryTold).where(StoryTold.persona_id == persona_id)
    ).scalars().all()
    for t in told:
        st = session.get(PersonaStory, t.event_id)
        if st is not None and (st.tellable_policy or "") == "never":
            errors.append(f"never_story_told: {t.event_id} -> {t.actor_id}")
    # 7. Quan điểm trái ngược trong cùng điều kiện: perspective chỉ có
    # một dòng active/aspect nên mâu thuẫn nội bộ không xảy ra ở DB;
    # nếu changed_view thiếu prior_content thì là warning (không phân
    # biệt được thay đổi vs mâu thuẫn).
    from ..models import PersonaPerspective
    persps = session.execute(
        select(PersonaPerspective).where(
            PersonaPerspective.persona_id == persona_id,
            PersonaPerspective.status == "active")).scalars().all()
    for p in persps:
        if p.aspect == "changed_view" and not p.prior_content:
            warnings.append(
                "changed_view_missing_prior: cần prior_content để phân biệt thay đổi vs mâu thuẫn")
    # 8. Thiếu thông tin cơ bản (warning, không phải error).
    if "vehicle_model" not in facts:
        warnings.append("missing_vehicle_model")
    if not stories:
        warnings.append("no_active_stories")
    return {"errors": errors, "warnings": warnings, "unknowns": unknowns}
