"""Normalize planner do model trả về (§5).

Luna thật không luôn trả đúng schema chặt của pipeline: ví dụ trace
ngày 30/10 cho thấy plan_turn có goal là câu tiếng Việt tự do
("Trả lời ngắn gọn màu xe nếu có dữ liệu..."), intent source="user",
type="question", response_direction có field lạ (tone,
max_followup_questions). Pipeline cũ reject toàn bộ plan rồi gửi lại
history thiếu assistant message -> OpenRouter trả 400.

Normalize ở đây là lớp dung sai, KHÔNG phải để model muốn gì cũng được:
- goal/source/subject/type được map về vocab có kiểm soát của Planner;
- field lạ bị bỏ, không đưa vào Planner;
- nếu sau normalize vẫn không parse được thì mới trả UNKNOWN để model
  làm lại, với history tool hợp lệ (xem pipeline).
"""
from __future__ import annotations

GOALS = {"answer_question", "share_experience", "clarify", "explore_need",
         "empathize", "stop", "handoff"}
TYPES = {"ask_persona", "ask_product", "share_need", "third_party",
         "correction", "negation", "opt_out", "transaction", "social",
         "ai_identity", "affirmation"}


def _goal(value) -> str:
    if value in GOALS:
        return value
    low = str(value or "").lower()
    if "dừng" in low or "stop" in low:
        return "stop"
    if "handoff" in low or "chuyển" in low or "nhân viên" in low:
        return "handoff"
    if "chia sẻ" in low or "trải nghiệm" in low or "share" in low:
        return "share_experience"
    if "làm rõ" in low or "clarify" in low:
        return "clarify"
    if "tìm hiểu" in low or "khám phá" in low or "explore" in low:
        return "explore_need"
    if "đồng cảm" in low or "empath" in low:
        return "empathize"
    return "answer_question"


def _source(value) -> str:
    low = str(value or "").lower()
    if "context" in low or "suy luận" in low or "inferred" in low:
        return "inferred_context"
    return "stated"  # "user", "stated", "khách nói" đều là nói rõ


def _subject(value) -> str:
    low = str(value or "").lower()
    if "persona" in low or "hoàng nam" in low:
        return "persona"
    if any(k in low for k in ("bố", "mẹ", "vợ", "chồng", "người khác")):
        return "third_person"
    return "self"


def _type(value, normalized: str) -> str:
    if value in TYPES:
        return value
    low = (str(value or "") + " " + str(normalized or "")).lower()
    if any(k in low for k in ("màu", "xe gì", "mua lúc", "kỷ niệm",
                              "đi xe", "chuyến đi")):
        return "ask_persona"
    if any(k in low for k in ("giá", "bảo hành", "thông số", "đại lý")):
        return "ask_product"
    if "muốn mua" in low or "cân nhắc" in low:
        return "share_need"
    if "đặt cọc" in low or "thanh toán" in low:
        return "transaction"
    if "không muốn" in low or "dừng" in low:
        return "opt_out"
    return "social"


def _data_needs(values) -> list[str]:
    out: list[str] = []
    for v in values or []:
        low = str(v).lower()
        if "stor" in low or "kỷ niệm" in low or "chuyến" in low:
            out.append("persona_stories")
        elif "persona" in low or "màu" in low:
            out.append("persona_facts")
        elif "product" in low or "giá" in low:
            out.append("product_facts")
        elif "customer" in low or "khách" in low:
            out.append("customer_state")
        elif "câu hỏi" in low or "question" in low:
            out.append("question_history")
        else:
            out.append(str(v))
    return out or ["none"]


def normalize_planner_args(args: dict) -> dict:
    args = args or {}
    direction = args.get("response_direction") or {}
    if not isinstance(direction, dict):
        direction = {}
    ask_followup = bool(direction.get("ask_followup", False))
    if direction.get("max_followup_questions") == 0:
        ask_followup = False
    intents = []
    for it in args.get("intents") or []:
        if not isinstance(it, dict):
            continue
        try:
            conf = float(it.get("confidence", 0.8))
        except (TypeError, ValueError):
            conf = 0.8
        intents.append({
            "type": _type(it.get("type"), it.get("normalized", "")),
            "subject": _subject(it.get("subject", "self")),
            "relation": it.get("relation"),
            "normalized": str(it.get("normalized", "")),
            "evidence": str(it.get("evidence", "")),
            "source": _source(it.get("source", "stated")),
            "confidence": min(1.0, max(0.0, conf)),
        })
    return {
        "intents": intents,
        "pending_questions": [str(x) for x in args.get("pending_questions") or []],
        "answered_question_keys": [
            str(x) for x in args.get("answered_question_keys") or []],
        "known_customer": args.get("known_customer") or {},
        "known_persona": args.get("known_persona") or {},
        "data_needs": _data_needs(args.get("data_needs")),
        "goal": _goal(args.get("goal")),
        "response_direction": {
            "answer_first": bool(direction.get("answer_first", True)),
            "ask_followup": ask_followup,
            "followup_purpose": direction.get("followup_purpose"),
            "followup_key": direction.get("followup_key"),
        },
        "resolved_reference": args.get("resolved_reference") or {},
    }
