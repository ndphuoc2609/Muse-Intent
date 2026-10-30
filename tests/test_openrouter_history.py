"""Hồi quy cho lỗi 400 trong trace ngày 30/10.

Nguyên nhân gốc (đã xác minh từ trace cậu gửi):
1. Pipeline cũ append tool message trực tiếp mà không có assistant
   message chứa tool_calls, cũng không có tool_call_id -> OpenAI/
   OpenRouter reject lần gọi thứ 2 với 400.
2. Luna trả plan_turn sai schema chặt: goal là câu tiếng Việt tự do,
   source="user", type="question", response_direction có field lạ
   (tone, max_followup_questions) -> Planner cũ reject, planner rỗng.

Test này dùng đúng plan_turn Luna đã gửi, và một provider giả lập
kiểm tra chuẩn OpenAI: tool message phải có tool_call_id khớp với
assistant message ngay trước đó, nếu không raise như OpenRouter 400.
"""
import json

from app.engine.planner_normalize import normalize_planner_args
from app.schemas import Planner
from app.providers.base import ProviderResult, ToolCall
from conftest import turn

LUNA_PLAN = {
    "intents": [{
        "type": "question",
        "subject": "xe Hyundai Creta của persona",
        "relation": "màu xe",
        "normalized": "Người dùng hỏi xe Creta của persona màu gì",
        "evidence": "Bác đi xe màu gì thế",
        "source": "user",
        "confidence": 1,
    }],
    "pending_questions": [],
    "answered_question_keys": [],
    "known_customer": {},
    "known_persona": {
        "name": "Hoàng Nam", "age": 24, "area": "Cầu Giấy, Hà Nội",
        "vehicle": "Hyundai Creta",
        "experience": "Mới lấy Creta đi làm hằng ngày, cuối tuần về quê",
    },
    "data_needs": [
        "Màu xe của persona; cần kiểm tra persona facts/stories, "
        "không tự suy đoán.",
    ],
    "goal": "Trả lời ngắn gọn màu xe nếu có dữ liệu được xác thực; "
            "nếu không thì nói rõ chưa có thông tin, không bịa.",
    "response_direction": {
        "tone": "Tiếng Việt tự nhiên, gần gũi; xưng mình/bạn",
        "max_followup_questions": 0,
    },
}


def test_luna_plan_normalizes_to_valid_planner():
    p = Planner(**normalize_planner_args(LUNA_PLAN))
    assert p.goal == "answer_question"
    assert p.intents[0].type == "ask_persona"
    assert p.intents[0].subject == "persona"
    assert p.intents[0].source == "stated"
    assert p.response_direction.ask_followup is False


class StrictProvider:
    """Giả lập kiểm tra của OpenAI: sai history -> raise như 400."""

    def __init__(self):
        self.calls = 0

    @staticmethod
    def _check_history(messages):
        for i, m in enumerate(messages):
            if m.get("role") == "tool":
                assert m.get("tool_call_id"), "tool message thiếu tool_call_id"
                prev = messages[i - 1]
                assert prev.get("role") == "assistant", \
                    "tool message không đứng sau assistant"
                ids = {tc["id"] for tc in prev.get("tool_calls", [])}
                assert m["tool_call_id"] in ids, \
                    "tool_call_id không khớp assistant tool_calls"

    def chat(self, messages, tools):
        self._check_history(messages)
        self.calls += 1
        if self.calls == 1:
            return ProviderResult(tool_calls=[
                ToolCall("plan_turn", LUNA_PLAN)])
        if self.calls == 2:
            return ProviderResult(tool_calls=[
                ToolCall("get_persona_facts", {})])
        # Lấy fact xe từ tool result để claim hợp lệ.
        vehicle_id = None
        for m in messages:
            if m.get("role") == "tool":
                try:
                    data = json.loads(m.get("content", "{}"))
                except Exception:
                    continue
                for f in data.get("data", []) or []:
                    if isinstance(f, dict) and \
                            f.get("fact_key") == "vehicle_model":
                        vehicle_id = f["id"]
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": "Mình đang đi Hyundai Creta đó bạn.",
            "claims": [{"kind": "persona", "ref_id": vehicle_id,
                        "value": "Hyundai Creta"}],
            "goal": "answer_question"})])


def test_pipeline_history_passes_openai_strict_check(pipe, session):
    from app.engine.pipeline import Pipeline
    from app.engine.fake_sender import FakeSender
    from app.schemas import TurnRequest

    strict = StrictProvider()
    p = Pipeline(session, strict, FakeSender())
    r = p.run(TurnRequest(
        persona_id="hoang-nam", post_text="Bài về Creta.",
        post_vehicle_models=["Hyundai Creta"],
        actor_id="actor-1", text="Bác đi xe màu gì thế?"))
    assert strict.calls >= 3
    assert r.status == "sent"
    assert r.planner["intents"][0]["type"] == "ask_persona"
