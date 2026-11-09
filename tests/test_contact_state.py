"""ContactState (Phase 1c): dừng/từ chối liên hệ/từ chối mua là ba
trạng thái độc lập, phát hiện deterministic trước mọi LLM call và
ghi ngay kể cả khi provider lỗi sau đó.
FakeProvider + SQLite in-memory, KHÔNG gọi OpenRouter."""
from sqlalchemy import select

from app.engine.contact_state import STOP_ACK_TEXT, detect_contact_signals
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.models import ContactState, LeadState
from app.providers.fake import FakeProvider
from conftest import turn


class BrokenProvider:
    def chat(self, messages, tools):
        raise RuntimeError("provider unavailable")


def _contact(session, thread_id, actor="actor-1"):
    return session.execute(
        select(ContactState).where(
            ContactState.thread_id == thread_id,
            ContactState.actor_id == actor)).scalars().first()


# --- detect_contact_signals: pure, token-sequence ---
def test_detect_stop_sets_stopped_and_refused():
    assert detect_contact_signals("Dừng lại đi") == {
        "conversation_status": "stopped", "contact_permission": "refused"}
    assert detect_contact_signals("Thôi đừng hỏi nữa") == {
        "conversation_status": "stopped", "contact_permission": "refused"}


def test_detect_contact_refusal_only():
    assert detect_contact_signals("Đừng liên hệ nữa") == {
        "contact_permission": "refused"}
    assert detect_contact_signals("Đừng gọi em") == {
        "contact_permission": "refused"}


def test_detect_purchase_refusal_only():
    assert detect_contact_signals("Em không mua đâu") == {
        "purchase_interest": "not_interested"}
    assert detect_contact_signals("Chỉ xem thôi") == {
        "purchase_interest": "not_interested"}


def test_detect_purchase_interest():
    assert detect_contact_signals("Em đang tính mua Creta") == {
        "purchase_interest": "interested"}


def test_detect_states_are_independent():
    # Muốn mua nhưng cấm gọi: cả hai key cùng ghi, không suy diễn lẫn nhau.
    assert detect_contact_signals("Em muốn mua nhưng đừng gọi em") == {
        "contact_permission": "refused", "purchase_interest": "interested"}


def test_detect_nothing_for_neutral_text():
    assert detect_contact_signals("Bác đang đi xe gì?") == {}
    # Khớp theo chuỗi token, không substring: "thời gian" không trigger.
    assert detect_contact_signals("Mất bao nhiêu thời gian thì tới?") == {}


# --- Pipeline: stop ack + ghi ngay kể cả khi provider lỗi ---
def test_stop_short_circuits_without_llm_call(pipe, session):
    pipe_broken = Pipeline(session, BrokenProvider(), FakeSender())
    r = turn(pipe_broken, "Dừng lại đi")
    assert r.status == "sent"
    assert r.reply_text == STOP_ACK_TEXT
    assert r.trace.get("contact_stop") is True
    row = _contact(session, r.thread_id)
    assert row is not None
    assert row.conversation_status == "stopped"
    assert row.contact_permission == "refused"
    # Stop không viết LeadState (planner không thấy lượt dừng).
    assert session.query(LeadState).filter_by(
        thread_id=r.thread_id).count() == 0


def test_contact_refusal_is_recorded_even_when_provider_fails(pipe, session):
    pipe_broken = Pipeline(session, BrokenProvider(), FakeSender())
    r = turn(pipe_broken, "Đừng liên hệ nữa")
    assert r.status == "error"
    row = _contact(session, r.thread_id)
    assert row is not None
    assert row.contact_permission == "refused"
    assert row.conversation_status is None  # từ chối liên hệ != dừng hẳn


# --- Latch: đã từ chối thì không đẩy sale; đổi ý thì mở lại ---
def test_lead_capture_blocked_after_purchase_refusal(pipe, session):
    r1 = turn(pipe, "Em không mua đâu")
    assert r1.status == "sent"
    assert _contact(session, r1.thread_id).purchase_interest == "not_interested"
    r2 = turn(pipe, "Cho em xin số liên hệ đại lý",
              thread_id=r1.thread_id)
    assert r2.trace["lead_capture"] is False


def test_lead_capture_allowed_without_refusal(pipe, session):
    r = turn(pipe, "Cho em xin số liên hệ đại lý")
    assert r.trace["lead_capture"] is True


def test_buying_signal_reopens_interest_but_keeps_contact_refusal(pipe, session):
    r1 = turn(pipe, "Dừng lại đi")
    assert r1.status == "sent"
    row = _contact(session, r1.thread_id)
    assert row.conversation_status == "stopped"
    assert row.contact_permission == "refused"
    # Khách nhắn lại: trò chuyện mở lại, quyền liên hệ vẫn refused.
    r2 = turn(pipe, "Chào bác", thread_id=r1.thread_id)
    assert r2.status == "sent"
    row = _contact(session, r1.thread_id)
    assert row.conversation_status == "active"
    assert row.contact_permission == "refused"
    # Khách đổi ý muốn mua: purchase_interest mở lại, nhưng quyền liên
    # hệ 'refused' vẫn giữ — sale vẫn bị chặn cho tới khi khách cho
    # phép rõ ràng.
    r3 = turn(pipe, "Em đang tính mua Creta", thread_id=r1.thread_id)
    assert r3.status == "sent"
    row = _contact(session, r1.thread_id)
    assert row.purchase_interest == "interested"
    assert row.contact_permission == "refused"
    assert r3.trace["lead_capture"] is False


def test_buying_signal_reopens_lead_capture_at_once(pipe, session):
    r1 = turn(pipe, "Em không mua đâu")
    assert _contact(session, r1.thread_id).purchase_interest == "not_interested"
    r2 = turn(pipe, "Cho em xin số liên hệ đại lý",
              thread_id=r1.thread_id)
    assert r2.trace["lead_capture"] is False
    # Lượt này có tín hiệu mua mới: detector đầu lượt ghi interested
    # ngay (trước khi tính _lead_capture) nên sale mở lại luôn.
    r3 = turn(pipe, "Em đang tính mua Creta", thread_id=r1.thread_id)
    assert r3.trace["lead_capture"] is True
    assert _contact(session, r1.thread_id).purchase_interest == "interested"
    r4 = turn(pipe, "Cho em xin số liên hệ đại lý",
              thread_id=r1.thread_id)
    assert r4.trace["lead_capture"] is True


def test_contact_state_is_per_actor(pipe, session):
    r1 = turn(pipe, "Em không mua đâu")
    row_a = _contact(session, r1.thread_id, "actor-1")
    assert row_a.purchase_interest == "not_interested"
    # Actor khác trong cùng thread không bị lây trạng thái.
    r2 = turn(pipe, "Cho em xin số liên hệ đại lý",
              thread_id=r1.thread_id, actor="actor-2")
    assert r2.trace["lead_capture"] is True
    assert _contact(session, r1.thread_id, "actor-2") is None


def test_describe_contact_state_mentions_refusal():
    from app.engine.contact_state import describe_contact_state
    assert describe_contact_state(None) == "(chưa có)"
    row = ContactState(thread_id="t", actor_id="a",
                       purchase_interest="not_interested",
                       contact_permission="refused")
    text = describe_contact_state(row)
    assert "KHÔNG mua" in text and "ĐỪNG LIÊN HỆ" in text
