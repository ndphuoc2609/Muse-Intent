"""SQLAlchemy models. Một schema duy nhất cho cả SQLite và Postgres.

Quy ước:
- Mọi bảng có status/version/created_at để giữ lịch sử, không xóa cứng.
- Story không bao giờ bị ghi đè: bổ sung chỉ qua StoryRevision (addendum/
  correction), body gốc giữ nguyên; canonical view = body + addenda active.
- CustomerFact không bao giờ bị xóa khi đổi ý: bản cũ chuyển status
  corrected/revoked, bản mới là current.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return uuid.uuid4().hex[:24]


class Base(DeclarativeBase):
    pass


class Persona(Base):
    __tablename__ = "personas"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    region: Mapped[str] = mapped_column(String(128), default="")
    stance: Mapped[str] = mapped_column(String(32), default="peer_user")
    style: Mapped[str] = mapped_column(Text, default="")
    identity_note: Mapped[str] = mapped_column(Text, default="")
    is_fictional: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PersonaFact(Base):
    __tablename__ = "persona_facts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    persona_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("personas.id"))
    fact_key: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="seed")
    status: Mapped[str] = mapped_column(String(32), default="active")
    valid_from: Mapped[str | None] = mapped_column(String(32), nullable=True)
    valid_to: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    provenance: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PersonaStory(Base):
    __tablename__ = "persona_stories"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    persona_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("personas.id"))
    title: Mapped[str] = mapped_column(String(256), default="")
    summary: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    entities: Mapped[dict] = mapped_column(JSON, default=dict)
    event_type: Mapped[str] = mapped_column(String(64), default="experience")
    event_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_date_precision: Mapped[str] = mapped_column(
        String(16), default="unknown")
    fictional_generated: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    told_in_message_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True)
    provenance: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class StoryRevision(Base):
    __tablename__ = "story_revisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    event_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("persona_stories.event_id"))
    persona_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))  # addendum | correction
    content: Mapped[str] = mapped_column(Text)
    entities: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="active")
    reason: Mapped[str] = mapped_column(Text, default="")
    provenance: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ProductFact(Base):
    __tablename__ = "product_facts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    model: Mapped[str] = mapped_column(String(128))
    trim: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fact_type: Mapped[str] = mapped_column(String(64))
    value_text: Mapped[str] = mapped_column(Text)
    value_number: Mapped[float | None] = mapped_column(nullable=True)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(128), default="sample")
    verified_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    valid_from: Mapped[str | None] = mapped_column(String(32), nullable=True)
    valid_to: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Actor(Base):
    __tablename__ = "actors"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))


class Thread(Base):
    __tablename__ = "threads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    persona_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("personas.id"))
    post_id: Mapped[str] = mapped_column(String(128), default="post-1")
    post_text: Mapped[str] = mapped_column(Text, default="")
    post_vehicle_models: Mapped[dict] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    thread_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("threads.id"))
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(32))  # user | persona | system
    text: Mapped[str] = mapped_column(Text)
    # draft | sent | blocked | review | error
    status: Mapped[str] = mapped_column(String(32), default="draft")
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True)
    trace: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_message_idem"),
    )


class CustomerFact(Base):
    __tablename__ = "customer_facts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    thread_id: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(64))
    subject: Mapped[str] = mapped_column(String(64))  # self | third_person
    relation: Mapped[str | None] = mapped_column(String(64), nullable=True)
    predicate: Mapped[str] = mapped_column(String(128))
    value_raw: Mapped[str] = mapped_column(Text)
    value_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[str] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(32))  # stated | inferred_context
    confidence: Mapped[float] = mapped_column(default=0.8)
    status: Mapped[str] = mapped_column(String(32), default="current")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class QuestionState(Base):
    __tablename__ = "question_states"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    thread_id: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(64))
    question_key: Mapped[str] = mapped_column(String(128))
    question_text: Mapped[str] = mapped_column(Text)
    asked_message_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True)
    # pending | answered | ignored | declined | redirected
    status: Mapped[str] = mapped_column(String(32), default="pending")
    answered_by_message_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True)
    answer_field: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class StoryTold(Base):
    """Story đã kể, theo dõi theo persona + actor + thread + message.

    told_in_message_id trên PersonaStory chỉ là lần kể đầu tiên (giữ
    để tương thích); bảng này mới là bản ghi đầy đủ: story được truy
    xuất lại và kể lại cũng có một dòng, không chỉ story vừa tạo.
    """
    __tablename__ = "story_told"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    event_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("persona_stories.event_id"))
    persona_id: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    thread_id: Mapped[str] = mapped_column(String(32))
    message_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("messages.id"))
    told_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (
        UniqueConstraint("event_id", "actor_id", "message_id",
                         name="uq_story_told_event_actor_message"),
    )


class TurnIdempotency(Base):
    """Idempotency theo scope (thread, actor, key), không phải toàn cục.

    Lý do tách bảng: Message.idempotency_key có unique toàn cục nên
    hai message (user + persona) cùng key đã vi phạm, và cùng key ở
    thread khác cũng vi phạm. Bảng này có unique theo scope, kèm
    request_hash để phân biệt cùng key khác payload -> conflict.
    Message.idempotency_key là legacy, các lượt mới để NULL.
    """
    __tablename__ = "turn_idempotency"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    thread_id: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    user_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    persona_message_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="in_progress")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (
        UniqueConstraint("thread_id", "actor_id", "idempotency_key",
                         name="uq_turn_idem_scope"),
    )


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    message_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("messages.id"), unique=True)
    sender: Mapped[str] = mapped_column(String(32), default="fake")
    status: Mapped[str] = mapped_column(String(32))  # sent | failed | unknown
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
