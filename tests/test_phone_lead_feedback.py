"""Hồi quy cho feedback transcript hội thoại Creta (Phước, 2026-11-04).

5 điểm trong transcript:
1. Bài viết chỉ nhắc Hyundai Creta, khách chưa nhắc mẫu khác mà persona
   vẫn hỏi "mẫu trong bài hay mẫu khác" -> prompt quy chiếu (không có
   check xác định nên chỉ kiểm tra prompt chứa luật, không test hành vi
   model).
2. Sale intro phải tự nhiên: hỏi khu vực trước, nhắc quen sale giá tốt,
   xin phép tham khảo (prompt, như trên).
3. "không tiện thì thôi nha" nói TRƯỚC khi khách từ chối là sai hoàn
   toàn -> validator §2e bắt cứng trong luồng lead.
4. Khách đưa "0123456789" mà persona xác nhận chuyển cho sale như số
   thật -> tool propose_customer_fact kiểm tra hình thức SĐT di động VN
   và trả CONFLICT để model hỏi lại, không lưu lead giả.
5. Trả lời trải nghiệm vòng vo ("chưa có trải nghiệm cụ thể để kể")
   -> prompt trả lời trực tiếp (như 1-2, chỉ kiểm tra prompt).
"""
import pytest

from app.engine.tools import ToolExecutor
from app.engine.typed_values import (
    is_plausible_vn_mobile,
    normalize_vn_mobile,
)
from app.engine.validator import validate
from app.models import CustomerFact


def test_normalize_vn_mobile():
    assert normalize_vn_mobile("0901234567") == "0901234567"
    assert normalize_vn_mobile("090 123 45 67") == "0901234567"
    assert normalize_vn_mobile("090.123.4567") == "0901234567"
    assert normalize_vn_mobile("+84901234567") == "0901234567"
    assert normalize_vn_mobile("84901234567") == "0901234567"
    assert normalize_vn_mobile("abc") is None
    assert normalize_vn_mobile("") is None


def test_plausible_vn_mobile_accepts_real_looking_numbers():
    # Số đẹp chứa dãy 123456 nhưng không đơn điệu toàn cục -> vẫn qua.
    assert is_plausible_vn_mobile("0912345678")
    assert is_plausible_vn_mobile("0901234567")
    assert is_plausible_vn_mobile("0321234567")
    assert is_plausible_vn_mobile("0701234567")
    assert is_plausible_vn_mobile("0881234567")
    assert is_plausible_vn_mobile("+84901234567")


def test_plausible_vn_mobile_rejects_fake_numbers():
    # Số trong transcript: đầu 01x đã chuyển đổi từ 2018 + dãy liên tiếp.
    assert not is_plausible_vn_mobile("0123456789")
    assert not is_plausible_vn_mobile("0987654321")
    assert not is_plausible_vn_mobile("0900000000")
    assert not is_plausible_vn_mobile("0111111111")
    assert not is_plausible_vn_mobile("090123456")
    assert not is_plausible_vn_mobile("09012345678")
    assert not is_plausible_vn_mobile("02838234567")


def _phone_result(session, number):
    ex = ToolExecutor(session, "AUTO_029", "t", "actor-1",
                      current_user_text=number)
    return ex.execute("propose_customer_fact", {
        "subject": "self", "predicate": "phone_number",
        "value_raw": number, "evidence": number, "source": "stated"})


def test_propose_fake_phone_conflicts_and_saves_nothing(session):
    r = _phone_result(session, "0123456789")
    assert r.status == "CONFLICT"
    assert "không hợp lệ" in (r.reason or "")
    assert session.query(CustomerFact).filter_by(
        predicate="phone_number").count() == 0


def test_propose_valid_phone_saves_pending(session):
    r = _phone_result(session, "0901234567")
    assert r.status == "FOUND"
    row = session.query(CustomerFact).filter_by(
        predicate="phone_number").one()
    assert row.status == "pending"
    assert row.value_raw == "0901234567"


def test_preemptive_refusal_out_blocked_in_lead_flow():
    final = {"text": "Nếu tiện, em để lại số điện thoại hoặc Zalo nhé; "
                     "không tiện thì thôi nha.",
             "goal": "answer_question", "claims": []}
    errors = validate(final, {"retrieved": {}, "lead_capture": True})
    assert any("preemptive_refusal_out" in e for e in errors)


def test_preemptive_refusal_out_not_blocked_outside_lead_flow():
    # Ngoài luồng lead thì câu này không thuộc §2e (và cũng không khớp
    # private_request vì không chứa cụm xin SĐT).
    final = {"text": "Không tiện thì thôi, để khi khác nhé.",
             "goal": "answer_question", "claims": []}
    errors = validate(final, {"retrieved": {}})
    assert not any("preemptive_refusal_out" in e for e in errors)


def test_sale_contact_guarantee_blocked_on_non_handoff():
    final = {"text": "Cảm ơn em nhé, mình sẽ chuyển số này để sale tư vấn "
                     "trực tiếp. Sale sẽ liên hệ theo thông tin em để lại.",
             "goal": "answer_question", "claims": []}
    errors = validate(final, {"retrieved": {}})
    assert any("transaction_confirmation" in e for e in errors)


def test_casual_confirmation_passes_validator():
    # Cách xác nhận Phước duyệt: giới thiệu sale nhắn tin + chúc, không
    # hứa sale chắc chắn liên hệ/gọi.
    final = {"text": "Oke, để mình giới thiệu sale nhắn tin với cậu xem thử, "
                     "chúc cậu sớm mua được xe.",
             "goal": "answer_question", "claims": []}
    errors = validate(final, {"retrieved": {}})
    assert not any("transaction_confirmation" in e for e in errors)
    assert not any("preemptive_refusal_out" in e for e in errors)


def test_prompt_rules_present():
    import re
    from app.engine.runtime_prompt import CONVERSATION_POLICY
    # Prompt nguồn xuống dòng giữa câu nên gộp whitespace trước khi so.
    policy = re.sub(r"\s+", " ", CONVERSATION_POLICY)
    assert "mẫu xe trong bài" in policy
    assert "không tiện thì thôi" in policy
    assert "chưa có trải nghiệm cụ thể để kể" in policy
    assert "giới thiệu sale nhắn tin với cậu xem thử" in policy
