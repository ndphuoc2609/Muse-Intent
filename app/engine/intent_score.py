"""Điểm tham khảo theo luật; không phải xác suất và không điều khiển gate."""
from __future__ import annotations

import re

from .gate import NO_REPLY_EXACT, normalize


_QUESTION = re.compile(
    r"\b(?:bao nhieu|bao lau|bao gio|khi nao|the nao|nhu nao|"
    r"lam sao|vi sao|tai sao|ra sao|o dau|mau gi|xe gi|may cho)\b"
)
_STOP = re.compile(
    r"\b(?:khong muon tiep tuc|dung lai|dung nhan nua|dung lien he|"
    r"ngung lien he|khong lien he nua|khong can nua|khong quan tam nua)\b"
)


def score_intent(text: str, *, has_pending: bool, has_lead: bool,
                 current_lead: bool, gate_action: str) -> dict:
    """Trả điểm, các thành phần trước khi giới hạn và phiên bản luật."""
    normalized = normalize(text or "")
    if not normalized:
        return {"score": 0, "factors": [{"label": "Không có chữ hoặc số", "points": 0}],
                "method": "rules_v1"}

    factors = [{"label": "Nội dung bình luận", "points": 40}]
    if len(normalized.split()) >= 4:
        factors.append({"label": "Có từ bốn từ trở lên", "points": 10})
    if "?" in text or "？" in text or _QUESTION.search(normalized):
        factors.append({"label": "Có dấu hiệu câu hỏi", "points": 20})
    if current_lead:
        factors.append({"label": "Có tín hiệu quan tâm trong lượt này", "points": 25})
    elif has_lead:
        factors.append({"label": "Có ngữ cảnh quan tâm trước đó", "points": 15})
    if has_pending:
        factors.append({"label": "Có câu hỏi đang chờ trả lời", "points": 30})
    if gate_action == "faq":
        factors.append({"label": "Khớp câu hỏi thường gặp", "points": 10})
    if _STOP.search(normalized):
        factors.append({"label": "Có yêu cầu dừng hoặc từ chối", "points": 20})
    if normalized in NO_REPLY_EXACT and not (has_pending or has_lead or current_lead):
        factors.append({"label": "Phản hồi ngắn không có ngữ cảnh cần tiếp tục", "points": -35})

    total = sum(factor["points"] for factor in factors)
    return {"score": min(100, max(0, total)), "factors": factors, "method": "rules_v1"}
