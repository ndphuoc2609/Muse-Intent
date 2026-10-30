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


def _strip_diacritics(text: str) -> str:
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", text or "")
        if unicodedata.category(c) != "Mn").lower()


def _model_token(model_value: str) -> str:
    """Token phân biệt của tên xe: từ cuối, VD 'Hyundai Tucson' -> 'tucson'."""
    parts = _strip_diacritics(model_value).replace("-", " ").split()
    return parts[-1] if parts else ""


def guard_resolved_reference(planner: dict, persona_vehicle: str | None,
                             user_text: str,
                             last_persona_text: str | None) -> dict:
    """Engine guard cho tham chiếu mơ hồ (lỗi live 30/10: Minh Anh lái
    Tucson, kể chuyện Tucson, khách nói 'Tôi cũng tính lấy 1 chiếc' nhưng
    planner resolve thành Creta theo xe trong bài viết).

    Chỉ downgrade khi ĐỦ cả 3 điều kiện, để không phá các trường hợp đúng:
    1. resolved_to khác xe của persona (so theo token phân biệt).
    2. Lượt khách hiện tại KHÔNG nêu tên xe đó (không có token trong text).
    3. Lượt persona gần nhất CÓ nhắc xe của persona (khách đang đáp lại
       câu chuyện của persona, nên neo gần nhất mạnh hơn neo bài viết).
    Khi downgrade: resolved_to=None, confidence <= 0.4, ambiguous_models
    gồm cả hai xe, kèm guard note để model thấy lý do và phải hỏi làm rõ
    hoặc nói chung, không được khẳng định tên xe nào. KHÔNG tự viết lại
    thành xe persona, vì '1 chiếc' vẫn có thể là xe trong bài — trạng thái
    trung thực là ambiguous, không phải một khẳng định khác.
    """
    resolved = dict(planner.get("resolved_reference") or {})
    resolved_to = resolved.get("resolved_to")
    if not resolved_to or not persona_vehicle:
        return planner
    persona_token = _model_token(persona_vehicle)
    resolved_token = _model_token(str(resolved_to))
    if not persona_token or not resolved_token:
        return planner
    if resolved_token == persona_token:
        return planner
    if resolved_token in _strip_diacritics(user_text or ""):
        return planner
    if not last_persona_text or persona_token not in _strip_diacritics(
            last_persona_text):
        return planner
    out = dict(planner)
    out["resolved_reference"] = {
        **resolved,
        "resolved_to": None,
        "confidence": min(float(resolved.get("confidence") or 0.7), 0.4),
        "ambiguous_models": [persona_vehicle, str(resolved_to)],
        "guard": "persona_vehicle_mismatch: tham chiếu mơ hồ, khách không nêu "
                 "tên xe, lượt persona gần nhất nhắc xe persona — không "
                 "được khẳng định tên xe, hỏi làm rõ hoặc nói chung",
    }
    return out
