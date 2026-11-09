"""Shadow score (intent_score_llm): model chấm mức ưu tiên chú ý
(0-100, cùng ý nghĩa với rules_v1, KHÔNG phải xác suất), chỉ ghi
trace + hiển thị, không điều khiển gate hay bất kỳ quyết định nào.
FakeProvider + SQLite in-memory, KHÔNG gọi OpenRouter."""
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.engine.planner_normalize import normalize_planner_args
from app.models import Message
from app.providers.fake import FakeProvider
from conftest import turn


def test_clamps_to_0_100_and_rejects_non_numbers():
    assert normalize_planner_args({"intent_score_llm": 150})["intent_score_llm"] == 100.0
    assert normalize_planner_args({"intent_score_llm": -20})["intent_score_llm"] == 0.0
    assert normalize_planner_args({"intent_score_llm": 65.5})["intent_score_llm"] == 65.5
    assert normalize_planner_args({})["intent_score_llm"] is None
    assert normalize_planner_args({"intent_score_llm": "cao"})["intent_score_llm"] is None
    assert normalize_planner_args({"intent_score_llm": True})["intent_score_llm"] is None


def test_shadow_score_stored_in_trace_next_to_rules_score(pipe, session):
    r = turn(pipe, "Em muốn mua xe")
    assert r.status == "sent"
    # FakeProvider chấm share_need = 75 (shadow); rules_v1 vẫn tính riêng.
    assert r.trace["intent_score_llm"] == 75
    assert r.trace["intent_score"]["method"] == "rules_v1"
    assert isinstance(r.trace["intent_score"]["score"], int)
    msg = session.get(Message, r.message_id)
    assert msg.trace["intent_score_llm"] == 75


def test_shadow_score_absent_when_model_does_not_return_it(session):
    from app.providers.base import ProviderResult, ToolCall

    class NoScoreProvider:
        def chat(self, messages, tools):
            import json
            called = {json.loads(m["content"]).get("tool") for m in messages
                      if m.get("role") == "tool"}
            if "plan_turn" not in called:
                return ProviderResult(tool_calls=[ToolCall("plan_turn", {
                    "intents": [{"type": "social", "normalized": "chào",
                                 "evidence": "chào"}],
                    "goal": "answer_question"})])
            return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
                "text": "Chào bạn.", "claims": [],
                "goal": "answer_question"})])

    r = turn(Pipeline(session, NoScoreProvider(), FakeSender()),
             "Chào buổi sáng")
    assert r.status == "sent"
    assert r.trace.get("intent_score_llm") is None


def test_shadow_score_does_not_change_gate(session):
    # Cùng input, score shadow khác nhau -> gate vẫn ra cùng quyết định.
    from app.providers.base import ProviderResult, ToolCall

    def provider_with_score(score):
        class P:
            def chat(self, messages, tools):
                import json
                called = {json.loads(m["content"]).get("tool") for m in messages
                          if m.get("role") == "tool"}
                if "plan_turn" not in called:
                    return ProviderResult(tool_calls=[ToolCall("plan_turn", {
                        "intents": [{"type": "ask_persona",
                                     "normalized": "hỏi xe",
                                     "evidence": "Bác đang đi xe gì?"}],
                        "goal": "answer_question",
                        "intent_score_llm": score})])
                return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
                    "text": "Mình đi Creta.", "claims": [],
                    "goal": "answer_question"})])
        return P()

    r_low = turn(Pipeline(session, provider_with_score(0), FakeSender()),
                 "Bác đang đi xe gì?")
    r_high = turn(Pipeline(session, provider_with_score(100), FakeSender()),
                  "Bác đang đi xe gì?")
    assert r_low.trace["intent_score_llm"] == 0.0
    assert r_high.trace["intent_score_llm"] == 100.0
    assert r_low.trace["gate"] == r_high.trace["gate"]
    assert r_low.status == r_high.status == "sent"
