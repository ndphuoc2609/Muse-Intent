"""Taxonomy category + tag ban đầu (version 1).

Quy tắc mở rộng: thêm định nghĩa mới có version vào đây + seed lại
tag_definitions; KHÔNG tự tạo tag từ lời model trong hội thoại. Tag lạ
do model đề xuất sẽ vào enrichment draft hoặc bị chặn ở tool, không
tự thành taxonomy.

Category không ép tính cách: hai persona cùng category có thể nói khác
nhau hoàn toàn; category chỉ hỗ trợ retrieval và thiết kế trải nghiệm.
Tag `family` không suy ra có con, cần xe 7 chỗ hay sắp mua xe — cần
fact cụ thể mới kết luận.
"""
from __future__ import annotations

TAXONOMY_VERSION = 1

# category_code -> (định nghĩa, điều kiện áp dụng)
CATEGORIES = {
    "first_car_owner": (
        "Chủ xe lần đầu: chưa có kinh nghiệm sở hữu/bảo dưỡng dài hạn, "
        "nhiều băn khoăn khi chọn xe đầu tiên.",
        "fact is_first_car hoặc story mua xe lần đầu."),
    "family_daily_use": (
        "Xe phục vụ gia đình hằng ngày: chở người thân, đi học/đi làm, "
        "đi chơi cuối tuần cùng gia đình.",
        "story/fact chở gia đình, đi cùng người thân."),
    "urban_commuter": (
        "Đi lại đô thị hằng ngày: đi làm trong phố, cần dễ xoay xở, dễ đỗ.",
        "commute_route trong phố, story đi phố hằng ngày."),
    "frequent_road_trip": (
        "Đi đường dài thường xuyên: về quê, du lịch, đường trường.",
        "story chuyến đi xa lặp lại, không phải một lần duy nhất."),
    "experienced_owner": (
        "Chủ xe có kinh nghiệm: đã sở hữu nhiều năm hoặc nhiều xe, "
        "quen bảo dưỡng, đánh giá xe theo trải nghiệm dài.",
        "sở hữu từ 3 năm trở lên hoặc đã đổi xe."),
    "ownership_cost_focused": (
        "Quan tâm chi phí sở hữu: xăng, bảo dưỡng, khấu hao, chi phí phát sinh.",
        "perspective ưu tiên chi phí hoặc story tính chi phí."),
    "technology_interested": (
        "Quan tâm công nghệ trên xe: an toàn chủ động, kết nối, tiện ích.",
        "story/fact dùng tính năng công nghệ, không phải khen chung chung."),
}

# tag -> (namespace, định nghĩa, điều kiện áp dụng, subject_scope)
TAGS = {
    "lifestyle.city_living": (
        "lifestyle", "Sống ở đô thị, sinh hoạt gắn với phố.",
        "region là đô thị, commute trong phố.", "persona"),
    "lifestyle.family_with_parents": (
        "lifestyle", "Thường xuyên đi cùng bố mẹ (không suy ra có con).",
        "story/fact đi cùng bố mẹ.", "persona,story"),
    "usage.city_commute": (
        "usage", "Đi làm/đi lại trong phố hằng ngày.",
        "commute_route hoặc story đi phố hằng ngày.", "persona,fact,story"),
    "usage.family_weekend": (
        "usage", "Đi chơi/đi cùng gia đình cuối tuần.",
        "story cuối tuần cùng gia đình.", "story"),
    "usage.long_distance_trip": (
        "usage", "Chuyến đi đường dài (về quê, du lịch).",
        "story event_type trip, quãng đường dài.", "story"),
    "usage.daily_school_run": (
        "usage", "Đưa đón đi học hằng ngày.",
        "fact/story đưa đón đi học, không suy từ tag family.", "fact,story"),
    "priority.running_cost": (
        "priority", "Ưu tiên chi phí vận hành thấp.",
        "perspective/fact nhắc chi phí xăng/bảo dưỡng.", "persona,fact,story"),
    "priority.parking_convenience": (
        "priority", "Ưu tiên dễ đỗ, dễ xoay xở trong phố.",
        "perspective/story đỗ xe, xoay xở.", "persona,story"),
    "priority.space_comfort": (
        "priority", "Ưu tiên không gian, thoải mái cho người ngồi.",
        "perspective/story không gian, ghế, hành lý.", "persona,story"),
    "priority.safety_long_drive": (
        "priority", "Ưu tiên an toàn khi đi đường dài.",
        "perspective/story an toàn đường trường.", "persona,story"),
    "concern.first_car_uncertainty": (
        "concern", "Băn khoăn của người mua xe lần đầu: sợ chọn sai.",
        "first_car_owner + story băn khoăn khi mua.", "persona,story"),
    "concern.parking_difficulty": (
        "concern", "Khó đỗ xe trong phố/chung cư.",
        "story/fact đỗ khó.", "fact,story"),
    "concern.maintenance_cost": (
        "concern", "Lo chi phí bảo dưỡng/phát sinh.",
        "story/fact bảo dưỡng, chi phí.", "fact,story"),
    "experience.long_distance_trip": (
        "experience", "Đã có trải nghiệm đi đường dài.",
        "story trip đã xảy ra, không phải ý định.", "story"),
    "experience.city_parking": (
        "experience", "Đã có trải nghiệm đỗ xe trong phố.",
        "story đỗ xe trong phố.", "story"),
    "experience.first_purchase": (
        "experience", "Trải nghiệm mua xe lần đầu.",
        "story mua xe đầu tiên.", "story"),
    "vehicle.suv_compact": (
        "vehicle", "Xe SUV cỡ nhỏ (ví dụ Creta).",
        "fact vehicle_model thuộc nhóm SUV cỡ nhỏ.", "fact"),
    "vehicle.suv_midsize": (
        "vehicle", "Xe SUV cỡ trung (ví dụ Tucson).",
        "fact vehicle_model thuộc nhóm SUV cỡ trung.", "fact"),
    "conversation_topic.family_trip": (
        "conversation_topic", "Chủ đề: đi chơi cùng gia đình.",
        "story/fact đi gia đình.", "story"),
    "conversation_topic.running_cost": (
        "conversation_topic", "Chủ đề: chi phí vận hành.",
        "story/fact chi phí.", "story"),
    "conversation_topic.parking": (
        "conversation_topic", "Chủ đề: đỗ xe.",
        "story/fact đỗ xe.", "story"),
}


def seed_taxonomy(session) -> dict:
    """Upsert tag_definitions từ TAGS (idempotent theo tag)."""
    from ..models import TagDefinition

    n = 0
    for tag, (ns, definition, applies, scope) in TAGS.items():
        row = session.get(TagDefinition, tag)
        if row is None:
            session.add(TagDefinition(
                tag=tag, namespace=ns, definition=definition,
                applies_when=applies, subject_scope=scope,
                version=TAXONOMY_VERSION))
            n += 1
        elif row.version != TAXONOMY_VERSION:
            row.definition = definition
            row.applies_when = applies
            row.subject_scope = scope
            row.version = TAXONOMY_VERSION
            n += 1
    session.commit()
    return {"taxonomy_version": TAXONOMY_VERSION, "upserted": n,
            "tags": len(TAGS), "categories": len(CATEGORIES)}


def is_known_tag(tag: str) -> bool:
    return tag in TAGS
