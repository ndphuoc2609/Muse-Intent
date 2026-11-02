"""Luật khai thác need: cấm câu hỏi chung chung khi khách vừa nêu need
cụ thể, và cấm chốt hội thoại chỉ vì khách nói "không" với câu hỏi
chung. Đoạn hội thoại gây ra luật này (2026-11-02): khách nêu "đi phố
tiết kiệm" -> persona hỏi chung "còn muốn hỏi thêm gì không?" ->
khách "không anh ạ" -> planner goal stop -> chốt sớm."""
from app.engine.runtime_prompt import CONVERSATION_POLICY
from app.engine.tool_schemas import TOOLS
from app.engine.validator import validate

GENERIC_REPLY = ("À, vậy điều em quan tâm nhất ở Creta là đi phố tiết kiệm. "
                 "Em còn muốn hỏi thêm gì về Creta không?")
GENERIC_Q = "Em còn muốn hỏi thêm gì về Creta không?"


def _final(text, **kw):
    d = {"text": text, "claims": []}
    d.update(kw)
    return d


def test_generic_followup_blocked_when_need_stated():
    errs = validate(
        _final(GENERIC_REPLY, followup_question=GENERIC_Q,
               followup_key="creta_more_questions"),
        {"retrieved": {}, "planner_intents": ["share_need"]})
    assert any(e.startswith("generic_followup_on_stated_need") for e in errs)


def test_generic_followup_allowed_without_stated_need():
    errs = validate(
        _final(GENERIC_REPLY, followup_question=GENERIC_Q,
               followup_key="creta_more_questions"),
        {"retrieved": {}, "planner_intents": ["social"]})
    assert not [e for e in errs if e.startswith("generic_followup")]


def test_specific_followup_allowed_with_stated_need():
    errs = validate(
        _final("Vậy em đi phố là chính. Mỗi ngày em chạy phố khoảng bao "
               "nhiêu km?",
               followup_question="Mỗi ngày em chạy phố khoảng bao nhiêu km?",
               followup_key="city_km"),
        {"retrieved": {}, "planner_intents": ["share_need"]})
    assert not [e for e in errs if e.startswith("generic_followup")]


def test_stop_on_bare_negation_after_generic_blocked():
    errs = validate(
        _final("Oke em, vậy mình dừng ở đây nhé.", goal="stop"),
        {"retrieved": {}, "user_text": "không anh ạ",
         "prev_persona_text": GENERIC_REPLY})
    assert any(e.startswith("stop_on_generic_refusal") for e in errs)


def test_genuine_stop_not_blocked():
    # Khách nói rõ muốn dừng -> chốt là đúng, không bị chặn.
    errs = validate(
        _final("Oke em, vậy mình dừng ở đây nhé.", goal="stop"),
        {"retrieved": {}, "user_text": "đừng hỏi nữa",
         "prev_persona_text": GENERIC_REPLY})
    assert not [e for e in errs if e.startswith("stop_on_generic")]
    # Câu hỏi trước là câu cụ thể -> "không" không rơi vào diện này.
    errs = validate(
        _final("Oke em, vậy mình dừng ở đây nhé.", goal="stop"),
        {"retrieved": {}, "user_text": "không anh ạ",
         "prev_persona_text": "Mỗi ngày em chạy phố khoảng bao nhiêu km?"})
    assert not [e for e in errs if e.startswith("stop_on_generic")]


def test_prompt_states_need_exploration_rule():
    assert "Khai thác need đã nêu" in CONVERSATION_POLICY
    plan_desc = next(t["function"]["description"] for t in TOOLS
                     if t["function"]["name"] == "plan_turn")
    assert "Khai thác need" in plan_desc
