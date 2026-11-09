"""Quyền liên hệ / ý định mua / trạng thái trò chuyện (Phase 1c).

Tách RIÊNG khỏi tín hiệu lead (has_lead_signal) và khỏi LeadState:
- purchase_interest: khách có ý mua không (interested | not_interested).
- contact_permission: có được liên hệ lại không (allowed | refused).
- conversation_status: cuộc trò chuyện còn tiếp không (active | stopped).
Ba trạng thái độc lập: khách có thể vẫn muốn mua nhưng không muốn bị
gọi ('Em muốn mua nhưng đừng gọi em'), hoặc hết ý mua nhưng vẫn trò
chuyện. Không suy diễn trạng thái này từ trạng thái kia.

Phát hiện DETERMINISTIC (không qua model) từ lượt khách hiện tại,
chạy trước mọi LLM call và commit ngay — kể cả khi provider lỗi sau
đó thì quyền liên hệ vẫn được ghi nhận. Chỉ khớp theo chuỗi token để
tránh 'gia đình' khớp 'giá' kiểu substring.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select

from ..models import ContactState

STOP_ACK_TEXT = (
    "Dạ, mình hiểu rồi, mình dừng ở đây nhé. "
    "Cảm ơn bạn đã trò chuyện!"
)

# Cụm từ dừng trò chuyện (khách yêu cầu dừng hẳn).
_STOP_PHRASES = (
    "dung lai", "khong muon tiep tuc", "khong muon tro chuyen",
    "khong muon noi chuyen", "de em tu tim hieu", "dung hoi nua",
    "thoi dung hoi", "khong muon nghe nua",
)
# Từ chối liên lạc lại (vẫn có thể đang trò chuyện, chỉ cấm liên hệ sau).
_CONTACT_REFUSED_PHRASES = (
    "dung lien he", "dung goi", "dung nhan", "khong can lien he",
    "khong muon ai goi", "khong muon ai lien he", "dung goi dien",
)
# Từ chối mua (không phải dừng trò chuyện).
_PURCHASE_REFUSED_PHRASES = (
    "khong mua dau", "khong mua nua", "khong co nhu cau mua",
    "khong dinh mua", "chi xem thoi", "chi tham khao thoi",
    "chua co nhu cau mua", "khong mua xe dau", "khong co y dinh mua",
)
# Ý định mua rõ ràng (để mở lại trạng thái sau khi từng từ chối).
_PURCHASE_INTERESTED_PHRASES = (
    "muon mua", "dang tim mua", "muon dat coc", "muon lay xe",
    "dang tinh mua", "muon mua mot chiec", "dang muon mua",
)


def _strip(text: str) -> str:
    s = "".join(
        c for c in unicodedata.normalize("NFD", text or "")
        if unicodedata.category(c) != "Mn").lower()
    return s.replace("đ", "d").replace("Đ", "d")


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", _strip(text))


def _has_phrase(tokens: list[str], phrase: str) -> bool:
    """Khớp theo chuỗi token liên tiếp (không substring)."""
    wt = phrase.split()
    return any(tokens[i:i + len(wt)] == wt
               for i in range(len(tokens) - len(wt) + 1))


def detect_contact_signals(text: str) -> dict:
    """Trả dict chỉ gồm trạng thái phát hiện được trong lượt này (key
    vắng = không suy ra gì, giữ nguyên trạng thái cũ). Dừng trò chuyện
    kéo theo từ chối liên hệ."""
    toks = _tokens(text)
    out: dict = {}
    if any(_has_phrase(toks, p) for p in _STOP_PHRASES):
        out["conversation_status"] = "stopped"
        out["contact_permission"] = "refused"
        return out
    if any(_has_phrase(toks, p) for p in _CONTACT_REFUSED_PHRASES):
        out["contact_permission"] = "refused"
    if any(_has_phrase(toks, p) for p in _PURCHASE_REFUSED_PHRASES):
        out["purchase_interest"] = "not_interested"
    elif any(_has_phrase(toks, p) for p in _PURCHASE_INTERESTED_PHRASES):
        out["purchase_interest"] = "interested"
    return out


def record_contact_signals(session, thread_id: str, actor_id: str,
                           detected: dict) -> ContactState | None:
    """Ghi trạng thái phát hiện được vào ContactState (chỉ ghi đè key
    có trong detected). Tạo row nếu chưa có. Commit ngay để quyền liên
    hệ được ghi nhận kể cả khi lượt này lỗi sau đó."""
    if not detected:
        return None
    row = session.execute(
        select(ContactState).where(
            ContactState.thread_id == thread_id,
            ContactState.actor_id == actor_id)).scalars().first()
    if row is None:
        row = ContactState(thread_id=thread_id, actor_id=actor_id)
        session.add(row)
    for key, value in detected.items():
        setattr(row, key, value)
    session.commit()
    return row


def load_contact_state(session, thread_id: str,
                       actor_id: str) -> ContactState | None:
    return session.execute(
        select(ContactState).where(
            ContactState.thread_id == thread_id,
            ContactState.actor_id == actor_id)).scalars().first()


def describe_contact_state(row: ContactState | None) -> str:
    """Một dòng cho prompt: model thấy quyền liên hệ để không đẩy sale
    sau khi khách đã từ chối. Row None = chưa có thông tin gì."""
    if row is None:
        return "(chưa có)"
    parts = []
    if row.purchase_interest == "not_interested":
        parts.append("khách đã nói KHÔNG mua — đừng giới thiệu sale, "
                     "đừng xin SĐT, đừng hẹn liên hệ mua bán")
    elif row.purchase_interest == "interested":
        parts.append("khách có ý mua")
    if row.contact_permission == "refused":
        parts.append("khách đã nói ĐỪNG LIÊN HỆ — đừng xin SĐT, đừng hẹn "
                     "gọi lại, chỉ trò chuyện trong thread này")
    if row.conversation_status == "stopped":
        parts.append("khách đã yêu cầu DỪNG trò chuyện")
    return "; ".join(parts) if parts else "(chưa có)"
