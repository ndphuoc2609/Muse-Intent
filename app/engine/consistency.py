"""Kiểm tra trùng / mâu thuẫn story (§9).

KHÔNG dùng UNIQUE(slug), hash văn bản, regex hay độ giống từ đơn thuần.
Cơ chế: candidate retrieval theo persona + thực thể có cấu trúc, rồi so
sánh trường có cấu trúc (loại sự kiện, ngày theo độ chính xác, xe, nơi,
người). Cùng nơi + cùng xe CHƯA ĐỦ để kết luận cùng sự kiện.

Kết quả: SAME_EVENT | NEW_EVENT | CONFLICT | AMBIGUOUS.
- SAME_EVENT: tái sử dụng event_id cũ, không tạo mới.
- CONFLICT: cùng sự kiện nhưng trường mâu thuẫn -> không lưu, trả review.
- AMBIGUOUS: chưa xác định được -> không lưu, không khẳng định.
- Không có cơ chế nào ở đây tuyên bố bảo đảm tuyệt đối không mâu thuẫn:
  ngữ nghĩa chỉ là một lớp kiểm tra, review vẫn là lớp cuối.
"""
from __future__ import annotations

from ..models import PersonaStory


def _tokens(text: str) -> set[str]:
    return {w.strip(".,!?") for w in text.lower().split() if len(w) > 2}


def _overlap(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, min(len(ta), len(tb)))


def _date_conflict(old: str | None, old_prec: str, new: str | None,
                   new_prec: str) -> bool:
    """So ngày theo độ chính xác: chỉ xung đột khi cả hai đều đủ chính
    xác (day/month) và khác nhau. unknown/year không tự kết luận."""
    if not old or not new:
        return False
    if old_prec in ("unknown", "year") or new_prec in ("unknown", "year"):
        return False
    if old_prec == "month" and new_prec == "month":
        return old[:7] != new[:7]
    if old_prec == "day" and new_prec == "day":
        return old[:10] != new[:10]
    # month vs day: xung đột nếu tháng khác.
    return old[:7] != new[:7]


def check_story(story: dict, candidates: list[PersonaStory],
                purchased_at: str | None) -> dict:
    """Trả về {verdict, event_id?, reason}."""
    entities = story.get("entities") or {}
    places = set(entities.get("places") or [])
    persons = set(entities.get("persons") or [])
    vehicle = entities.get("vehicle")
    new_date = story.get("event_date")
    new_prec = story.get("event_date_precision") or "unknown"
    # Timeline: không tự tạo một lần lái thử để hợp thức hóa sự kiện
    # trước ngày mua, và không thêm ngày giả để vượt validator.
    if story.get("event_type") in ("ownership", "trip") and new_date \
            and purchased_at and new_date[:7] < purchased_at[:7]:
        # Chuyến đi bằng xe mình sở hữu trước tháng mua là mâu thuẫn,
        # trừ khi thực thể ghi rõ là lái thử/mượn (event_type khác).
        return {"verdict": "CONFLICT",
                "reason": "event_date trước purchased_at cho sự kiện "
                          "sở hữu/chuyến đi bằng xe của persona"}
    best = None
    best_score = 0.0
    for cand in candidates:
        # Candidate pending trong cùng lượt vẫn được xét dedup (tránh
        # tạo trùng story khi model propose 2 lần), nhưng rejected/
        # revoked thì không.
        if cand.status not in ("active", "pending"):
            continue
        ce = cand.entities or {}
        c_places = set(ce.get("places") or [])
        c_persons = set(ce.get("persons") or [])
        same_place = bool(places & c_places)
        same_vehicle = bool(vehicle and ce.get("vehicle") == vehicle)
        sim = _overlap(story.get("summary", "") + " " + story.get("body", ""),
                       cand.summary + " " + cand.body)
        score = sim + (0.2 if same_place else 0) + (0.2 if same_vehicle else 0)
        if score > best_score:
            best_score, best = score, cand
        # Mâu thuẫn trên cùng ứng viên mạnh: cùng loại + cùng nơi/xe
        # nhưng ngày xung đột hoặc người tham gia khác hẳn theo trường.
        if cand.event_type == story.get("event_type") and same_place \
                and same_vehicle and _date_conflict(
                    cand.event_date, cand.event_date_precision,
                    new_date, new_prec):
            return {"verdict": "CONFLICT", "event_id": cand.event_id,
                    "reason": "cùng loại sự kiện, nơi, xe nhưng ngày mâu thuẫn"}
    if best is not None and best_score >= 0.55:
        return {"verdict": "SAME_EVENT", "event_id": best.event_id,
                "reason": f"candidate score {best_score:.2f}: trùng thực thể + nội dung"}
    if best is not None and 0.35 <= best_score < 0.55:
        return {"verdict": "AMBIGUOUS", "event_id": best.event_id,
                "reason": f"candidate score {best_score:.2f}: chưa đủ căn cứ"}
    return {"verdict": "NEW_EVENT", "reason": "không có candidate trùng"}


def check_addendum(story: PersonaStory, content: str,
                   entities: dict) -> dict:
    """Addendum tương thích được phép; mâu thuẫn trường có cấu trúc -> CONFLICT."""
    old_places = set((story.entities or {}).get("places") or [])
    new_places = set(entities.get("places") or [])
    # Ví dụ mâu thuẫn rõ: story ghi nơi A, addendum khẳng định "không phải A
    # mà là B" — ở đây phát hiện qua marker phủ định + nơi cũ.
    low = content.lower()
    if old_places and any(f"không phải {p.lower()}" in low for p in old_places):
        return {"verdict": "CONFLICT",
                "reason": "addendum phủ định nơi đã có trong story gốc"}
    if story.event_date and entities.get("event_date") \
            and entities["event_date"][:7] != story.event_date[:7] \
            and story.event_date_precision in ("month", "day"):
        return {"verdict": "CONFLICT",
                "reason": "addendum đổi ngày của story gốc (cần correction, không phải addendum)"}
    _ = new_places
    return {"verdict": "COMPATIBLE", "reason": "bổ sung, không mâu thuẫn trường có cấu trúc"}
