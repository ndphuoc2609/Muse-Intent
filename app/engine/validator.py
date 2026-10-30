"""Validator + policy (§12). Phân biệt loại claim, không ép lời nối
chuyện thành claim, không chặn fact phụ đã có nguồn chỉ vì khác key.

Quy tắc chính:
- Claim persona/product/story phải có ref_id đã được tool trả trong lượt
  và giá trị khớp dữ kiện (so khớp chuẩn hóa, không bắt exact string).
- Claim thiếu nguồn -> lỗi có cấu trúc, pipeline sửa tối đa N vòng,
  hết vòng -> review (không tự thay bằng câu chung chung sai ý khách).
- Cam kết giao dịch / xin SĐT / địa chỉ nhà ở kênh public -> chặn.
- Hỏi lại điều khách đã từ chối/opt-out -> chặn.
- Khi khách hỏi trực tiếp có phải AI không, reply phải chứa disclosure.
"""
from __future__ import annotations

import re

from ..config import SETTINGS

TRANSACTION_PHRASES = [
    "đã đặt cọc", "đã giữ xe", "đã chốt xe", "nhân viên sẽ gọi",
    "nhân viên sẽ liên hệ", "đã thanh toán", "xe đã được giữ",
]
PRIVATE_REQUEST_PHRASES = [
    "cho mình số điện thoại", "cho xin số điện thoại", "địa chỉ nhà của bạn",
    "gửi số điện thoại", "cho mình địa chỉ nhà",
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().strip())


def _value_matches(claim_value: str, fact_value: str) -> bool:
    c, f = _norm(claim_value), _norm(fact_value)
    if not c or not f:
        return False
    if c in f or f in c:
        return True
    ct, ft = set(c.split()), set(f.split())
    return len(ct & ft) / max(1, min(len(ct), len(ft))) >= 0.5


def validate(final: dict, ctx: dict) -> list[str]:
    """ctx: retrieved facts/stories/products (dict ref_id -> value),
    planner, opted_out, refused_keys. Trả list lỗi; rỗng = đạt."""
    errors: list[str] = []
    text = final.get("text", "") or ""
    low = _norm(text)
    goal = final.get("goal", "")
    retrieved = ctx.get("retrieved", {})  # ref_id -> (kind, value, meta)
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
    # 6. Claim có dữ kiện phải có nguồn phù hợp.
    for claim in final.get("claims", []) or []:
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
                if not _value_matches(value, rvalue):
                    errors.append(f"product_value_mismatch: claim '{value}' "
                                  f"khác fact '{rvalue}'")
            elif kind == "persona":
                if not _value_matches(value, rvalue):
                    errors.append(f"persona_value_mismatch: claim '{value}' "
                                  f"khác fact '{rvalue}'")
            elif kind == "story":
                # Story claim được phép paraphrase, nhưng không được thêm
                # sự kiện mới chưa lưu: kiểm tra overlap với canonical.
                if not _value_matches(value, rvalue):
                    errors.append(f"story_unsaved_detail: claim story '{value}' "
                                  "không khớp canonical view đã lưu")
    return errors
