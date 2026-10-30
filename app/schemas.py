"""Contract: planner, tool call/result, final reply. Version 1.

Mọi tool result đều có status rõ ràng: FOUND | NOT_FOUND | CONFLICT |
UNKNOWN | AMBIGUOUS. UNKNOWN (timeout/lỗi DB/lỗi tool) KHÔNG BAO GIỜ
được coi là NOT_FOUND để sáng tác (§7).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ToolStatus = Literal["FOUND", "NOT_FOUND", "CONFLICT", "UNKNOWN", "AMBIGUOUS"]
TurnGoal = Literal[
    "answer_question", "share_experience", "clarify", "explore_need",
    "empathize", "stop", "handoff",
]
TurnStatus = Literal["sent", "draft", "blocked", "review", "error"]


class IntentItem(BaseModel):
    type: str  # ask_persona | ask_product | share_need | third_party |
    # correction | negation | opt_out | transaction | social | ai_identity ...
    subject: str = "self"  # self | third_person | persona
    relation: str | None = None  # bố, mẹ, vợ, chồng, bạn... khi subject=third_person
    normalized: str
    evidence: str
    source: Literal["stated", "inferred_context"] = "stated"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class ResponseDirection(BaseModel):
    answer_first: bool = True
    ask_followup: bool = False
    followup_purpose: str | None = None
    followup_key: str | None = None


class Planner(BaseModel):
    intents: list[IntentItem] = []
    pending_questions: list[str] = []
    # Câu hỏi cũ được lượt này trả lời (key), kèm evidence; engine vẫn
    # kiểm chứng lại trước khi đánh dấu ANSWERED (§10).
    answered_question_keys: list[str] = []
    known_customer: dict[str, Any] = {}
    known_persona: dict[str, Any] = {}
    data_needs: list[str] = []  # persona_facts | persona_stories |
    # product_facts | customer_state | question_history | none
    goal: TurnGoal = "answer_question"
    response_direction: ResponseDirection = ResponseDirection()
    resolved_reference: dict[str, Any] = {}
    # VD: {"text": "xe này", "resolved_to": "Hyundai Creta",
    #      "source": "inferred_context", "confidence": 0.7}


class Claim(BaseModel):
    kind: Literal["persona", "story", "product", "customer_echo"]
    ref_id: str | None = None  # fact_id / event_id / product_fact_id
    value: str


class FinalReply(BaseModel):
    text: str
    claims: list[Claim] = []
    followup_question: str | None = None
    followup_key: str | None = None
    goal: TurnGoal = "answer_question"
    status: Literal["final", "insufficient_data"] = "final"


class ToolResult(BaseModel):
    status: ToolStatus
    tool: str
    data: Any = None
    ref_id: str | None = None
    source: str | None = None
    version: int | None = None
    valid_until: str | None = None
    limits: str | None = None
    reason: str | None = None


class TurnRequest(BaseModel):
    persona_id: str
    post_id: str = "post-1"
    post_text: str = ""
    post_vehicle_models: list[str] = []
    actor_id: str
    actor_name: str = "Khách"
    text: str
    thread_id: str | None = None
    message_id: str | None = None


class TurnResponse(BaseModel):
    status: TurnStatus
    thread_id: str
    message_id: str | None = None
    reply_text: str | None = None
    planner: dict | None = None
    tool_calls: list[dict] = []
    saved_customer_facts: list[dict] = []
    saved_stories: list[dict] = []
    validator_errors: list[str] = []
    trace: dict = {}
