"""Reply tự nhiên: cấm câu tuyên bố thiếu dữ liệu/thông số kiểu hệ
thống; lượt tư vấn phải có luật mời mở cuối lượt (conversation planning)."""
from app.engine.runtime_prompt import CONVERSATION_POLICY
from app.engine.validator import validate
from app.providers.openrouter import SYSTEM_PROMPT


def test_validator_bans_data_disclaimer():
    bad = validate(
        {"text": "Mình chưa có dữ liệu để so sánh nơi nào giá tốt nhất.",
         "claims": []},
        {"retrieved": {}})
    assert any(e.startswith("data_disclaimer") for e in bad)
    bad2 = validate(
        {"text": "Mình chưa có dữ liệu thông số để kết luận chắc hơn.",
         "claims": []},
        {"retrieved": {}})
    assert any(e.startswith("data_disclaimer") for e in bad2)


def test_validator_allows_natural_hedge():
    ok = validate(
        {"text": "Giá thì mỗi đại lý mỗi khác, để sale báo chính xác cho em.",
         "claims": []},
        {"retrieved": {}})
    assert not [e for e in ok if e.startswith("data_disclaimer")]
    ok2 = validate(
        {"text": "Cái này mình không rõ lắm, em hỏi sale cho chắc nhé.",
         "claims": []},
        {"retrieved": {}})
    assert not [e for e in ok2 if e.startswith("data_disclaimer")]


def test_prompt_bans_disclaimer_and_requires_open_invite():
    assert "KHÔNG nói kiểu \"mình chưa có dữ liệu/thông số" \
        in CONVERSATION_POLICY
    assert "lời mời mở nhẹ nhàng" in CONVERSATION_POLICY
    assert "em còn muốn hỏi gì thêm về xe không?" in CONVERSATION_POLICY
    assert "đừng tuyên bố \"chưa có dữ liệu/thông số\"" in SYSTEM_PROMPT
