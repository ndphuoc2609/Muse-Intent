"""Seed persona mẫu (§15). Dữ liệu HƯ CẤU, gắn nhãn rõ ràng.

Hoàng Nam, 24, Cầu Giấy — Hà Nội, Hyundai Creta 1.5 Cao cấp,
mua tháng 03/2024, xe đầu đời, đi làm Cầu Giấy – Mỹ Đình, cuối tuần
có thể về Nam Định. Những chi tiết CHƯA CÓ (màu xe, chuyến đi cụ thể)
KHÔNG được seed sẵn: chúng chỉ xuất hiện qua propose -> engine check
-> lưu (§8), đúng như yêu cầu.
"""
from __future__ import annotations

from .models import Actor, Persona, PersonaFact, ProductFact


def seed(session) -> dict:
    if session.get(Persona, "hoang-nam") is not None:
        return {"seeded": False, "persona_id": "hoang-nam"}
    p = Persona(
        id="hoang-nam", display_name="Hoàng Nam", age=24,
        region="Cầu Giấy, Hà Nội", stance="peer_user",
        style="Nói tự nhiên, gần gũi, xưng mình/bạn, chia sẻ trải nghiệm "
              "thật ngắn gọn, không rao bán, không hứa hẹn giao dịch.",
        identity_note="PERSONA HƯ CẤU cho sandbox, không phải người thật.",
        is_fictional=True)
    session.add(p)
    facts = [
        ("vehicle_model", "Hyundai Creta", "seed"),
        ("vehicle_trim", "1.5 Cao cấp", "seed"),
        ("purchased_at", "2024-03", "seed"),
        ("is_first_car", "xe đầu đời", "seed"),
        ("commute_route", "Cầu Giấy – Mỹ Đình", "seed"),
        ("weekend_route", "Cầu Giấy – Nam Định (thỉnh thoảng)", "seed"),
    ]
    for key, value, src in facts:
        session.add(PersonaFact(persona_id="hoang-nam", fact_key=key,
                                value=value, source=src))
    # Product facts MẪU, is_sample=True, không phải giá thực tế (§11).
    session.add(ProductFact(
        model="Hyundai Creta", trim="1.5 Cao cấp", fact_type="price",
        value_text="599 triệu đồng (giá mẫu, chưa phải giá thực tế)",
        value_number=599, unit="triệu đồng", location="Hà Nội",
        source="sample", valid_from="2026-01-01", valid_to="2026-12-31",
        is_sample=True))
    session.add(ProductFact(
        model="Hyundai Creta", trim="1.5 Cao cấp", fact_type="warranty",
        value_text="5 năm hoặc 100.000 km (dữ liệu mẫu)",
        source="sample", valid_from="2026-01-01", valid_to="2026-12-31",
        is_sample=True))
    session.add(Actor(id="actor-1", display_name="Khách 1"))
    session.add(Actor(id="actor-2", display_name="Khách 2"))
    session.commit()
    return {"seeded": True, "persona_id": "hoang-nam"}
