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
    # Episodic memory có cấu trúc (§4): các trường này phục vụ lưu trữ
    # và retrieval, lời nói có thể chỉ dùng một chi tiết, không bắt mọi
    # story đi theo khuôn kể chuyện cứng nhắc. NULL = chưa biết.
    participants: Mapped[dict] = mapped_column(JSON, default=list)
    context_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    concern_or_choice: Mapped[str | None] = mapped_column(
        Text, nullable=True)
    action_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    feeling_or_lesson: Mapped[str | None] = mapped_column(
        Text, nullable=True)
    canonical_facts: Mapped[dict] = mapped_column(JSON, default=list)
    # tellable_policy: always | on_relevance | never. NULL = on_relevance.
    # never thì retrieval không bao giờ trả về để kể.
    tellable_policy: Mapped[str | None] = mapped_column(
        String(32), nullable=True)
    related_need_topics: Mapped[dict] = mapped_column(JSON, default=list)
    # Category của story (không phải category của persona): lưu riêng,
    # không nhét vào tags để tránh nhầm tag taxonomy với category.
    story_categories: Mapped[dict] = mapped_column(JSON, default=list)
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
    # Customer need state (§9): need_kind phân biệt explicit /
    # preference / concern / constraint / intended_user / usage_context /
    # hypothesis / declined / opt_out. Hypothesis KHÔNG được nâng thành
    # fact chỉ vì model nhắc lại nhiều lần; certainty: high|medium|low.
    # NULL ở các fact cũ = suy ra từ predicate (xem tools/runtime).
    need_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    certainty: Mapped[str | None] = mapped_column(String(32), nullable=True)
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


class PersonaProfile(Base):
    """Hồ sơ nền có cấu trúc (không nhét tất cả vào một JSON).

    NULL = chưa biết (unknown), khác với giá trị "none"/"" = không có.
    Ví dụ: ownership_status NULL = chưa rõ có xe hay không; "not_owning"
    = đã xác lập là chưa sở hữu. Không suy diễn trường NULL thành fact.
    """
    __tablename__ = "persona_profiles"
    persona_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("personas.id"), primary_key=True)
    occupation: Mapped[str | None] = mapped_column(Text, nullable=True)
    daily_rhythm: Mapped[str | None] = mapped_column(Text, nullable=True)
    family_structure: Mapped[str | None] = mapped_column(Text, nullable=True)
    ownership_status: Mapped[str | None] = mapped_column(
        String(64), nullable=True)
    purchase_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    spending_limit_note: Mapped[str | None] = mapped_column(
        Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PersonaPerspective(Base):
    """Góc nhìn có chiều sâu, một dòng active cho mỗi aspect.

    aspect: values | tradeoffs | concerns | changed_view | unknowns |
    not_suitable_for. changed_view phải có prior_content (quan điểm cũ)
    để phân biệt "thay đổi theo thời gian" với "mâu thuẫn".
    """
    __tablename__ = "persona_perspectives"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    persona_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("personas.id"))
    aspect: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    prior_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PersonaStyleGuide(Base):
    """Phong cách giao tiếp. style_examples CHỈ hướng dẫn cách nói,
    không tự trở thành bằng chứng về đời sống persona."""
    __tablename__ = "persona_style_guides"
    persona_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("personas.id"), primary_key=True)
    address_form: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_length: Mapped[str | None] = mapped_column(Text, nullable=True)
    intimacy: Mapped[str | None] = mapped_column(Text, nullable=True)
    emoji_use: Mapped[str | None] = mapped_column(Text, nullable=True)
    storytelling: Mapped[str | None] = mapped_column(Text, nullable=True)
    proactive_question_level: Mapped[str | None] = mapped_column(
        Text, nullable=True)
    avoid_patterns: Mapped[dict] = mapped_column(JSON, default=list)
    style_examples: Mapped[dict] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PersonaCategory(Base):
    """Category nhiều-nhiều: một persona có thể thuộc nhiều category,
    category không ép cùng tính cách hay câu trả lời."""
    __tablename__ = "persona_categories"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    persona_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("personas.id"))
    category_code: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (
        UniqueConstraint("persona_id", "category_code",
                         name="uq_persona_category"),
    )


class TagDefinition(Base):
    """Taxonomy tag có namespace + định nghĩa. Mở rộng taxonomy chỉ
    bằng cách thêm định nghĩa mới có version, không tự tạo tag từ
    lời model trong hội thoại (tag lạ vào draft, xem enrichment)."""
    __tablename__ = "tag_definitions"
    tag: Mapped[str] = mapped_column(String(128), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(64))
    definition: Mapped[str] = mapped_column(Text)
    applies_when: Mapped[str] = mapped_column(Text, default="")
    subject_scope: Mapped[str] = mapped_column(String(128), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class TagAssignment(Base):
    """Gán tag cho một thực thể (persona/fact/story). Status pending
    trong hội thoại, chỉ active khi delivery thành công (cùng lifecycle
    với fact/story), rejected khi lượt review/error."""
    __tablename__ = "tag_assignments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    entity_type: Mapped[str] = mapped_column(String(32))  # persona|fact|story
    entity_id: Mapped[str] = mapped_column(String(128))
    tag: Mapped[str] = mapped_column(
        String(128), ForeignKey("tag_definitions.tag"))
    source: Mapped[str] = mapped_column(String(32), default="seed")
    status: Mapped[str] = mapped_column(String(32), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "tag",
                         name="uq_tag_assignment"),
    )


class EnrichmentDraft(Base):
    """Enrichment draft: ở trạng thái draft cho tới khi validator +
    publish chấp nhận. Không tự thành canon khi chỉ được đề xuất."""
    __tablename__ = "enrichment_drafts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    persona_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("personas.id"))
    kind: Mapped[str] = mapped_column(
        String(32))  # fact|story|perspective|tag|category|profile
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    anchor_refs: Mapped[dict] = mapped_column(JSON, default=list)
    proposed_tags: Mapped[dict] = mapped_column(JSON, default=list)
    proposed_categories: Mapped[dict] = mapped_column(JSON, default=list)
    conditions: Mapped[dict] = mapped_column(JSON, default=list)
    conflicts: Mapped[dict] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(32), default="generated")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    validator_errors: Mapped[dict] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class StoryLink(Base):
    """Liên kết giữa các story: retell | addendum | correction | supersedes.
    Correction/supersedes không xóa story cũ, chỉ ghi quan hệ + lịch sử."""
    __tablename__ = "story_links"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    from_event_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("persona_stories.event_id"))
    to_event_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("persona_stories.event_id"))
    link_type: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (
        UniqueConstraint("from_event_id", "to_event_id", "link_type",
                         name="uq_story_link"),
    )


class RetrievalLog(Base):
    """Vì sao một memory được truy xuất (cho admin xem, không phải canon).
    Ghi ở bước retrieval của pipeline, trước khi model quyết định kể hay không."""
    __tablename__ = "retrieval_logs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    persona_id: Mapped[str] = mapped_column(String(64))
    thread_id: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(default=0.0)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    message_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("messages.id"), unique=True)
    sender: Mapped[str] = mapped_column(String(32), default="fake")
    status: Mapped[str] = mapped_column(String(32))  # sent | failed | unknown
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
