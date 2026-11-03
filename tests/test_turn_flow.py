"""Response lượt: flow tóm tắt đọc được + trace không trùng lặp.
Người đọc chỉ cần đọc flow là nắm được luồng, JSON đầy đủ để sau."""
from tests.conftest import turn

from app.engine.pipeline import build_flow


def test_flow_on_sent_turn(pipe):
    r = turn(pipe, "Chào bạn")
    assert r.status == "sent"
    assert r.flow, "flow không được rỗng"
    assert r.flow[0].startswith("Khách:")
    assert any(l.startswith("Hướng:") for l in r.flow)
    assert any(l.startswith("Trả lời:") for l in r.flow)
    assert any(l == "Kiểm tra: 0 lỗi" for l in r.flow)
    assert any(l.startswith("Chi phí:") for l in r.flow)
    # Flow cũng nằm trong trace để replay idempotent thấy được.
    assert r.trace.get("flow") == r.flow


def test_flow_saved_fact_line(pipe):
    r = turn(pipe, "Em đang tìm hiểu Creta, muốn mua xe đi phố tiết kiệm")
    assert r.saved_customer_facts, "lượt share_need phải lưu fact khách"
    assert any(l.startswith("Lưu:") for l in r.flow)


def test_build_flow_phone_and_error_labels():
    flow = build_flow(
        {"intents": [{"type": "transaction",
                      "normalized": "Cung cấp SĐT để sale tư vấn Creta"}]},
        {"goal": "answer_question", "text": "Cảm ơn em, mình ghi nhận nhé."},
        [{"predicate": "phone_number", "value_normalized": "0358757899",
          "status": "pending"}],
        [], ["fake_sender_failed"], 0.001207, 9.467, 3)
    assert flow[0].startswith("Khách: Cung cấp SĐT")
    assert "Lưu: SĐT = 0358757899 (chờ gửi xong mới chính thức)" in flow
    assert "Kiểm tra: fake_sender_failed" in flow
    assert flow[-1].startswith("Chi phí: $0.001207")
