"""Kịch bản lead (Phước 2026-11-02, sửa theo GPT review 2026-11-09):
khai thác need tối đa 1-2 câu rồi DỪNG khai thác, trả lời tự nhiên —
KHÔNG tự đẩy sang giới thiệu sale + xin SĐT. Sale/đại lý chỉ khi
khách yêu cầu rõ (đặt cọc/giữ xe/kết nối đại lý/sale) hoặc đồng ý rõ
ràng khi được hỏi. Gate lead bật với "đang nghiên cứu mẫu xe"."""
from app.engine.rag import has_lead_signal
from app.engine.runtime_prompt import CONVERSATION_POLICY
from app.engine.tool_schemas import TOOLS
from app.engine.validator import validate

GENERIC_Q = "Em còn muốn hỏi thêm gì về Creta không?"


def test_lead_gate_research_phase():
    assert has_lead_signal("Em cũng đang nghiên cứu creta") is True
    assert has_lead_signal("em chỉ xem thôi") is False
    # "tìm hiểu" chung chung KHÔNG bật gate (dễ nhầm khách xem chơi).
    assert has_lead_signal("em đang tìm hiểu xe") is False


def test_generic_followup_before_cap_asks_specific():
    errs = validate(
        {"text": "Em còn muốn hỏi thêm gì về Creta không?",
         "claims": [], "followup_question": GENERIC_Q,
         "followup_key": "creta_more"},
        {"retrieved": {}, "planner_intents": ["share_need"],
         "asked_count": 1})
    assert any(e.startswith("generic_followup_on_stated_need") for e in errs)


def test_generic_followup_at_cap_stops_without_sale_push():
    # Hết trần khai thác: câu chung chung vẫn là lỗi soft, nhưng KHÔNG
    # còn lỗi exploration_cap đẩy sang sale + SĐT như kịch bản cũ.
    errs = validate(
        {"text": "Em còn muốn hỏi thêm gì về Creta không?",
         "claims": [], "followup_question": GENERIC_Q,
         "followup_key": "creta_more"},
        {"retrieved": {}, "planner_intents": ["share_need"],
         "asked_count": 2})
    assert any(e.startswith("generic_followup_on_stated_need") for e in errs)
    assert not any(e.startswith("exploration_cap") for e in errs)
    assert not any("sale" in e for e in errs)


def test_stop_refusal_at_cap_stops_without_sale_push():
    # Khách từ chối câu hỏi chung: chỉ dừng khai thác, trả lời tự
    # nhiên — không đẩy sale/SĐT.
    errs = validate(
        {"text": "Oke em, vậy mình dừng ở đây nhé.", "claims": [],
         "goal": "stop"},
        {"retrieved": {}, "user_text": "không anh ạ",
         "prev_persona_text": GENERIC_Q, "asked_count": 2})
    assert any(e.startswith("stop_on_generic_refusal") for e in errs)
    assert not any("sale" in e for e in errs)
    assert not any("SĐT" in e for e in errs)


def test_prompt_states_script():
    assert "tối đa 1-2 câu" in CONVERSATION_POLICY
    assert "nhắn riêng" in CONVERSATION_POLICY
    plan_desc = next(t["function"]["description"] for t in TOOLS
                     if t["function"]["name"] == "plan_turn")
    assert "1-2 câu" in plan_desc
    assert "KHÔNG tự đẩy sang giới thiệu sale + xin SĐT" in plan_desc
    assert "CHỈ khi khách yêu cầu rõ" in plan_desc
