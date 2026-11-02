"""Hồi quy: model claim cùng một story hai lần trong một final thì lượt
vẫn sent, chỉ ghi đúng một StoryTold (live Luna 2026-11-02 claim
AUTO_029_mem_3 hai lần -> UNIQUE constraint failed ở commit, cả lượt
chết dù reply đã gửi xong phần model)."""
import json

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.models import PersonaStory, StoryTold
from app.providers.base import ProviderResult, ToolCall
from app.schemas import TurnRequest
from sqlalchemy import select


class _DupClaimProvider:
    def _called(self, messages):
        called = set()
        for m in messages:
            if m.get("role") == "tool":
                try:
                    called.add(json.loads(m.get("content", "{}")).get("tool"))
                except Exception:
                    continue
        return called

    def chat(self, messages, tools):
        called = self._called(messages)
        if "plan_turn" not in called:
            return ProviderResult(tool_calls=[ToolCall("plan_turn", {
                "intents": [{"type": "ask_persona", "subject": "persona",
                             "relation": "chuyến đi",
                             "normalized": "Hỏi về đường ngập",
                             "evidence": "Trời mưa đường ngập thì sao?",
                             "source": "stated", "confidence": 1}],
                "pending_questions": [], "answered_question_keys": [],
                "known_customer": {}, "known_persona": {},
                "data_needs": ["persona_stories"],
                "goal": "share_experience",
                "response_direction": {"answer_first": True,
                                       "ask_followup": False}})])
        if "get_persona_stories" not in called:
            return ProviderResult(tool_calls=[ToolCall(
                "get_persona_stories", {})])
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": "Mình luôn chọn quay đầu thay vì liều qua đoạn ngập.",
            "claims": [{"kind": "story", "ref_id": "AUTO_029_mem_3",
                        "value": "Thảo luôn chọn quay đầu thay vì liều qua đoạn ngập"},
                       {"kind": "story", "ref_id": "AUTO_029_mem_3",
                        "value": "Thảo luôn chọn quay đầu thay vì liều qua đoạn ngập"}],
            "goal": "share_experience"})])


def test_duplicate_story_claim_writes_one_storytold(session):
    story = session.get(PersonaStory, "AUTO_029_mem_3")
    assert story is not None
    pipe = Pipeline(session, _DupClaimProvider(), FakeSender())
    r = pipe.run(TurnRequest(
        persona_id="AUTO_029", post_text="Bài về Creta.",
        post_vehicle_models=["Hyundai Creta"],
        actor_id="actor-1", text="Trời mưa đường ngập thì sao?"))
    assert r.status == "sent"
    rows = session.execute(select(StoryTold).where(
        StoryTold.event_id == "AUTO_029_mem_3",
        StoryTold.actor_id == "actor-1",
        StoryTold.message_id == r.message_id)).scalars().all()
    assert len(rows) == 1
