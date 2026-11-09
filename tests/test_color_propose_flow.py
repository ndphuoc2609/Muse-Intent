"""Màu xe khi canon chưa có: KHÔNG tự chọn màu rồi khẳng định (luồng
propose_persona_fact trong lượt chat đã bỏ — canon chỉ bổ sung qua
luồng admin draft flow). Persona trả lời tự nhiên là chưa chắc; khi
canon đã có màu thì trả đúng màu đó từ fact, không tạo thêm.

Thay thế luồng cũ (30/10: propose màu mặc định trắng trong lượt) đã
bị gỡ theo review GPT 2026-11-09: seed là người thật, prompt không còn
quyền chọn màu mặc định.
"""
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.engine.tools import ToolExecutor
from app.models import PersonaFact
from app.providers.fake import FakeProvider
from app.providers.openrouter import SYSTEM_PROMPT
from conftest import make_persona, turn


def test_system_prompt_forbids_inventing_persona_details():
    # Prompt mới: cấm tự chọn giá trị khi canon thiếu, canon chỉ bổ
    # sung qua luồng admin; không còn chỉ thị màu mặc định trắng hay
    # quyền gọi propose_persona_fact trong lượt.
    assert "KHÔNG được tự chọn một giá trị hợp lý" in SYSTEM_PROMPT
    assert "mặc định: trắng" not in SYSTEM_PROMPT
    assert "propose_persona_fact" not in SYSTEM_PROMPT
    assert "find_gaps" in SYSTEM_PROMPT


def test_color_unknown_answered_naturally_without_inventing(session):
    # AUTO_029 không có vehicle_color trong canon.
    r = turn(Pipeline(session, FakeProvider(), FakeSender()),
             "Bác đi xe màu gì thế?")
    assert r.status == "sent", f"validator_errors={r.validator_errors}"
    assert "chưa chắc" in (r.reply_text or "")
    assert "trắng" not in (r.reply_text or "")
    assert not r.validator_errors
    assert session.query(PersonaFact).filter_by(
        persona_id="AUTO_029", fact_key="vehicle_color").count() == 0


def test_color_from_canon_is_remembered(session):
    pid = make_persona(session, "auto-color-canon",
                       {"vehicle_model": "Hyundai Creta",
                        "vehicle_color": "trắng"})
    r = turn(Pipeline(session, FakeProvider(), FakeSender()),
             "Bác đi xe màu gì thế?", persona_id=pid)
    assert r.status == "sent", f"validator_errors={r.validator_errors}"
    assert "trắng" in (r.reply_text or "")
    rows = session.query(PersonaFact).filter_by(
        persona_id=pid, fact_key="vehicle_color").all()
    assert len(rows) == 1 and rows[0].value == "trắng"


def test_persona_fact_tool_no_longer_exists_in_turn(session):
    # propose_persona_fact đã gỡ khỏi tool của lượt chat: engine trả
    # UNKNOWN, không ghi gì vào DB, canon cũ giữ nguyên.
    pid = make_persona(session, "auto-color-canon-2",
                       {"vehicle_model": "Hyundai Creta",
                        "vehicle_color": "trắng"})
    ex = ToolExecutor(session, pid, "thread-x", "actor-1")
    res = ex.execute("propose_persona_fact",
                     {"fact_key": "vehicle_color", "value": "đen",
                      "source": "generated"})
    assert res.status == "UNKNOWN"
    row = session.query(PersonaFact).filter_by(
        persona_id=pid, fact_key="vehicle_color",
        status="active").first()
    assert row.value == "trắng"
