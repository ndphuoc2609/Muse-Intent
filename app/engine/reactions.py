"""Reactions kiểu Facebook cho comment trong sandbox.

Luật: mỗi actor tối đa 1 reaction mỗi message. Bấm cùng emoji thì gỡ,
bấm emoji khác thì đổi. Logic tách riêng khỏi API để test được bằng
session fixture, không cần HTTP.
"""
from __future__ import annotations

from sqlalchemy import select

from ..models import Message, MessageReaction

ALLOWED_EMOJIS = ("👍", "❤️", "😆", "😮", "😢", "😡")
EMOJI_NAMES = {"👍": "Thích", "❤️": "Yêu thích", "😆": "Haha",
               "😮": "Wow", "😢": "Buồn", "😡": "Phẫn nộ"}


def _message_counts(session, message_id: str) -> dict:
    counts: dict[str, int] = {}
    for row in session.execute(
            select(MessageReaction).where(
                MessageReaction.message_id == message_id)).scalars().all():
        counts[row.emoji] = counts.get(row.emoji, 0) + 1
    return counts


def toggle_reaction(session, thread_id: str, message_id: str,
                    actor_id: str, emoji: str) -> dict:
    """Bấm reaction: thêm / đổi / gỡ. Trả về trạng thái của message đó."""
    if emoji not in ALLOWED_EMOJIS:
        raise ValueError(f"emoji không hỗ trợ: {emoji}")
    if not actor_id:
        raise ValueError("thiếu actor_id")
    msg = session.get(Message, message_id)
    if msg is None or msg.thread_id != thread_id:
        raise LookupError("message không thuộc thread này")
    row = session.execute(
        select(MessageReaction).where(
            MessageReaction.message_id == message_id,
            MessageReaction.actor_id == actor_id)).scalars().first()
    if row is not None and row.emoji == emoji:
        session.delete(row)
        action = "removed"
    elif row is not None:
        row.emoji = emoji
        action = "changed"
    else:
        session.add(MessageReaction(message_id=message_id,
                                    actor_id=actor_id, emoji=emoji))
        action = "added"
    session.commit()
    mine = None if action == "removed" else emoji
    return {"message_id": message_id, "action": action,
            "counts": _message_counts(session, message_id), "mine": mine}


def get_reactions(session, thread_id: str, actor_id: str = "") -> dict:
    """Tổng hợp cả thread: counts theo message, mine theo actor xem."""
    msg_ids = [r.id for r in session.execute(
        select(Message).where(
            Message.thread_id == thread_id)).scalars().all()]
    counts: dict[str, dict] = {}
    mine: dict[str, str] = {}
    for row in session.execute(
            select(MessageReaction).where(
                MessageReaction.message_id.in_(msg_ids))).scalars().all():
        counts.setdefault(row.message_id, {})
        counts[row.message_id][row.emoji] = \
            counts[row.message_id].get(row.emoji, 0) + 1
        if actor_id and row.actor_id == actor_id:
            mine[row.message_id] = row.emoji
    return {"counts": counts, "mine": mine}
