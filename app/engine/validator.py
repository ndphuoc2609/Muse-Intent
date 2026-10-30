"""Validator + policy (§12, cải thiện §4.1/§4.2).

Hai chiều kiểm tra, không chỉ tin lời model:
1. Claim khai báo phải có nguồn đúng (ref_id trong tool result của lượt,
   đúng kind, giá trị khớp theo KIỂU — xem typed_values).
2. Khẳng định có dữ kiện trong văn bản cuối phải được bao phủ bởi một
   claim/bằng chứng phù hợp. Câu có tiền/ngày/%/km/màu xe/sở hữu xe mà
   claims=[] sẽ KHÔNG qua nữa.

Phân biệt để không chặn nhầm câu tự nhiên:
- Câu hỏi (kết thúc bằng ?) không phải khẳng định.
- Phản ứng xã giao, ý kiến/chủ quan ("mình thấy êm", "mình thích") không
  chứa mẫu dữ kiện có cấu trúc thì không cần claim.
- Lời nhắc lại ý khách ("bạn nói ...") không tính là khẳng định của persona.
- Phủ định ("không phải 599 triệu") không tính là khẳng định 599 triệu.
- Không yêu cầu từ nối, lời đồng cảm, câu hỏi phải có fact ID.

Dữ liệu mẫu: claim product là sample mà văn bản không ghi rõ là mẫu
thì bị chặn — không để giá mẫu lẫn với giá thật.
"""
from __future__ import annotations

import re

from ..config import SETTINGS
from .typed_values import (
    find_money, parse_dates, parse_distance_km, parse_percent,
    strip_claim_wrappers, typed_match, value_matches,
)

TRANSACTION_PHRASES = [
    "đã đặt cọc", "đã giữ xe", "đã chốt xe", "nhân viên sẽ gọi",
    "nhân viên sẽ liên hệ", "đã thanh toán", "xe đã được giữ",
]
PRIVATE_REQUEST_PHRASES = [
    "cho mình số điện thoại", "cho xin số điện thoại", "địa chỉ nhà của bạn",
    "gửi số điện thoại", "cho mình địa chỉ nhà",
]
_ECHO_MARKERS = ("bạn nói", "bạn bảo", "theo bạn", "bạn nhắc", "bạn kể")
_NEGATIONS = ("không phải", "không có", "chưa phải", "không phải là")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _sentences(text: str) -> list[tuple[str, bool]]:
    """Tách câu, kèm cờ có phải câu hỏi không."""
    parts = re.split(r"(?<=[.!?…?])\s+", text.strip())
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append((p, "?" in p))
    return out


def _is_negated_before(sentence: str, pos: int) -> bool:
    head = _norm(sentence[:pos])
    return any(n in head for n in _NEGATIONS)


def _claim_covers_typed(claims: list[dict], parser, raw: str) -> bool:
    """Một claim nào đó có giá trị cùng kiểu và bằng raw không?"""
    for c in claims or []:
        v = c.get("value", "")
        if parser(v) and parser(raw):
            m = typed_match(v, raw)
            if m:
                return True
    return False


def _uncovered_errors(text: str, claims: list[dict], retrieved: dict) -> list[str]:
    errors: list[str] = []
    claims = claims or []
    for sentence, is_question in _sentences(text):
        if is_question:
            continue
        low = _norm(sentence)
        if any(m in low for m in _ECHO_MARKERS):
            continue
        # Tiền.
        for m in re.finditer(
                r"\d[\d.,]*\s*(tỷ|triệu|tr|nghìn|đồng|đ|vnd)(?!\w)|\d{1,3}(?:\.\d{3}){2,}",
                sentence, re.IGNORECASE):
            if _is_negated_before(sentence, m.start()):
                continue
            raw = m.group(0).strip()
            if not _claim_covers_typed(claims, find_money_raw, raw):
                errors.append(
                    f"uncovered_factual_assertion: tiền '{raw}' trong câu "
                    "không có claim/bằng chứng phù hợp")
        # Phần trăm, quãng đường, ngày.
        for m in re.finditer(r"\d+(?:[.,]\d+)?\s*%", sentence):
            if _is_negated_before(sentence, m.start()):
                continue
            if not _claim_covers_typed(claims, parse_percent, m.group(0)):
                errors.append(
                    f"uncovered_factual_assertion: phần trăm '{m.group(0)}' "
                    "không có claim/bằng chứng phù hợp")
        for m in re.finditer(r"\d+(?:[.,]\d+)?\s*km\b", sentence, re.IGNORECASE):
            if _is_negated_before(sentence, m.start()):
                continue
            if not _claim_covers_typed(claims, parse_distance_km, m.group(0)):
                errors.append(
                    f"uncovered_factual_assertion: quãng đường '{m.group(0)}' "
                    "không có claim/bằng chứng phù hợp")
        for d in parse_dates(sentence):
            raw = _date_raw(sentence, d)
            if raw and not _claim_covers_typed(claims, parse_dates, raw):
                errors.append(
                    f"uncovered_factual_assertion: ngày/tháng '{raw}' "
                    "không có claim/bằng chứng phù hợp")
        # Màu xe: "màu X" là khẳng định trực tiếp về màu. Một claim bao
        # phủ màu khi: claim đó trỏ tới fact có giá trị đúng màu đó
        # (ref_id đã được tool trả về), hoặc claim value sau khi bỏ từ
        # đệm ("Xe của mình màu trắng" -> "trắng") bằng đúng màu đó.
        for m in re.finditer(r"\bmàu\s+(\w+)", sentence, re.IGNORECASE):
            color = _norm(m.group(1))
            covered = False
            for c in claims:
                if c.get("ref_id") in retrieved:
                    _k, _v, _meta = retrieved[c["ref_id"]]
                    if _norm(_v) == color:
                        covered = True
                        break
                if strip_claim_wrappers(c.get("value", "")) == color:
                    covered = True
                    break
            if not covered:
                errors.append(
                    f"uncovered_factual_assertion: màu xe '{color}' "
                    "không có claim/bằng chứng phù hợp")
        # Sở hữu xe: chỉ khi có động từ sở hữu + giá trị model của persona
        # trong retrieved (không hardcode tên mẫu xe).
        if any(v in low for v in ("đang đi", "mình đi", "xe mình", "xe của mình")):
            for ref_id, (kind, value, meta) in retrieved.items():
                if kind == "persona" and meta.get("fact_key") == "vehicle_model":
                    if _norm(value) and _norm(value) in low:
                        if not any(c.get("ref_id") == ref_id
                                   or _norm(value) in _norm(c.get("value", ""))
                                   for c in claims):
                            errors.append(
                                "uncovered_factual_assertion: mẫu xe persona "
                                f"'{value}' không có claim/bằng chứng phù hợp")
    return errors


def find_money_raw(text: str) -> list[int]:
    return [v for _, v in find_money(text)]


def _date_raw(sentence: str, d: tuple) -> str:
    # Trả về chuỗi ngày đầu tiên trong câu khớp với d (đủ để parse lại).
    for m in re.finditer(
            r"20\d{2}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/20\d{2}|20\d{2}-\d{1,2}"
            r"|tháng\s*\d{1,2}\s*(?:năm\s*)?20\d{2}|\d{1,2}/20\d{2}",
            sentence):
        if parse_dates(m.group(0)) and parse_dates(m.group(0))[0] == d:
            return m.group(0)
    return ""


def _story_matches(claim_value: str, fact_value: str) -> bool:
    typed = typed_match(claim_value, fact_value)
    if typed is not None:
        return typed
    ct = set(_norm(claim_value).split())
    ft = set(_norm(fact_value).split())
    if not ct or not ft:
        return False
    return len(ct & ft) / max(1, min(len(ct), len(ft))) >= 0.5


def validate(final: dict, ctx: dict) -> list[str]:
    """ctx: retrieved facts/stories/products (dict ref_id -> value),
    planner, opted_out, refused_keys. Trả list lỗi; rỗng = đạt."""
    errors: list[str] = []
    text = final.get("text", "") or ""
    low = _norm(text)
    goal = final.get("goal", "")
    retrieved = ctx.get("retrieved", {})  # ref_id -> (kind, value, meta)
    claims = final.get("claims", []) or []
    # 1. Cam kết giao dịch ngoài quyền.
    if goal != "handoff":
        for p in TRANSACTION_PHRASES:
            if p in low:
                errors.append(f"transaction_confirmation: chứa '{p}' "
                              "nhưng lượt này không phải handoff")
    # 2. Xin thông tin riêng tư ở kênh public.
    for p in PRIVATE_REQUEST_PHRASES:
        if p in low:
            errors.append(f"private_request: '{p}' không được hỏi ở kênh public")
    # 3. Opt-out: không hỏi thêm, không gợi mở.
    if ctx.get("opted_out"):
        if final.get("followup_question"):
            errors.append("opt_out_followup: khách muốn dừng nhưng vẫn hỏi thêm")
    # 4. Hỏi lại key khách đã từ chối.
    for key in ctx.get("refused_keys", []):
        if final.get("followup_key") == key:
            errors.append(f"refused_question: hỏi lại '{key}' khách đã từ chối")
    # 5. AI identity: phải minh bạch.
    if ctx.get("asked_ai_identity") and SETTINGS.ai_disclosure[:20].lower() \
            not in low and "persona hư cấu" not in low and "không phải người thật" not in low:
        errors.append("ai_identity: khách hỏi trực tiếp có phải AI không "
                      "nhưng reply thiếu disclosure")
    # 6. Claim có dữ kiện phải có nguồn phù hợp (kiểm tra theo kiểu).
    for claim in claims:
        kind, ref_id, value = claim.get("kind"), claim.get("ref_id"), \
            claim.get("value", "")
        if kind in ("persona", "story", "product"):
            if not ref_id or ref_id not in retrieved:
                errors.append(
                    f"unsourced_claim: claim {kind} '{value}' không có "
                    "ref_id trong tool result của lượt này")
                continue
            rkind, rvalue, meta = retrieved[ref_id]
            if rkind != kind:
                errors.append(f"claim_kind_mismatch: {ref_id} là {rkind}, "
                              f"không phải {kind}")
            if kind == "product":
                if meta.get("status") != "active":
                    errors.append(f"product_inactive: fact {ref_id} "
                                  f"status={meta.get('status')}")
                if meta.get("valid_to") and meta.get("now", "") > meta["valid_to"]:
                    errors.append(f"product_expired: fact {ref_id} đã hết hạn")
                if meta.get("is_sample") and "mẫu" not in low \
                        and "chưa phải giá thực tế" not in low \
                        and "không phải giá thực tế" not in low:
                    errors.append(
                        f"product_sample_unlabelled: fact {ref_id} là dữ liệu "
                        "mẫu nhưng reply không ghi rõ là mẫu")
                if not value_matches(value, rvalue):
                    errors.append(f"product_value_mismatch: claim '{value}' "
                                  f"khác fact '{rvalue}' (so theo kiểu)")
            elif kind == "persona":
                if not value_matches(value, rvalue):
                    errors.append(f"persona_value_mismatch: claim '{value}' "
                                  f"khác fact '{rvalue}' (so theo kiểu)")
            elif kind == "story":
                if not _story_matches(value, rvalue):
                    errors.append(f"story_unsaved_detail: claim story '{value}' "
                                  "không khớp canonical view đã lưu")
    # 7. Khẳng định có dữ kiện trong văn bản phải được claim bao phủ.
    errors.extend(_uncovered_errors(text, claims, retrieved))
    return errors
