"""Gate luat + FAQ: unit (khong can DB) + tich hop qua Pipeline.

Quy tac pin o day: override (pending, lead) luon thang truoc moi luat
tiet kiem; luot gate chan thi provider KHONG duoc goi (chung minh bang
provider se raise neu bi goi).
"""
import pytest

from app.engine.fake_sender import FakeSender
from app.engine.gate import decide_gate, load_bank, match_faq, normalize
from app.engine.pipeline import Pipeline
from app.models import Delivery, Message, QuestionState
from app.providers.fake import FakeProvider
from app.schemas import TurnRequest
from tests.conftest import turn


class _NoCallProvider:
    def chat(self, messages, tools):
        raise AssertionError("gate phai chan truoc khi goi provider")


class _CountingProvider:
    def __init__(self, inner):
        self.inner = inner
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        return self.inner.chat(messages, tools)


def _req(text, thread_id=None):
    return TurnRequest(persona_id="AUTO_029", post_text="Bài về Creta.",
                       post_vehicle_models=["Hyundai Creta"],
                       actor_id="actor-1", text=text, thread_id=thread_id)


# ---------- unit: chuan hoa ----------

def test_normalize_maps_d_and_strips_emoji():
    assert normalize("Chào Bác!") == "chao bac"
    assert normalize("Đi phố") == "di pho"  # d map rieng, NFD khong tach duoc
    assert normalize("👍👍") == ""
    assert normalize("  ok   nhé ") == "ok nhe"


# ---------- unit: bypass ----------

@pytest.mark.parametrize("text", ["", "   ", "👍👍", "!!!", "ok", "Ok nhé",
                                  "haha", "vâng ạ", "ừ", "da"])
def test_bypass_rules(text):
    d = decide_gate(text, has_pending=False, has_lead=False, bank=[])
    assert d.action == "bypass"


# ---------- unit: override thang truoc luat ----------

def test_pending_and_lead_override_bypass():
    d = decide_gate("ok", has_pending=True, has_lead=False, bank=[])
    assert d.action == "pipeline"
    d = decide_gate("ok", has_pending=False, has_lead=True, bank=[])
    assert d.action == "pipeline"


def test_lead_overrides_faq_greeting():
    bank = load_bank()
    d = decide_gate("Chào bác, em hỏi giá Creta", has_pending=False,
                    has_lead=True, bank=bank)
    assert d.action == "pipeline"


# ---------- unit: FAQ ----------

def test_faq_matches_and_narrowness():
    bank = load_bank()
    assert {e["id"] for e in bank} == {"fuel-generic", "city-driving",
                                       "warranty", "greeting", "thanks"}
    assert decide_gate("Bác đi Creta tốn xăng không?", False, False,
                        bank).action == "faq"
    assert decide_gate("Bác đi Creta tốn xăng không?", False, False,
                        bank).faq_id == "fuel-generic"
    assert decide_gate("Chào bác", False, False, bank).faq_id == "greeting"
    assert decide_gate("Cảm ơn bác", False, False, bank).faq_id == "thanks"
    assert decide_gate("Bảo hành mấy năm", False, False,
                        bank).faq_id == "warranty"
    assert decide_gate("Đi phố có ổn không", False, False,
                        bank).faq_id == "city-driving"
    # Exact hep: them tu la khong khop greeting nua.
    assert decide_gate("Chào bác nhé", False, False, bank).action == "pipeline"
    # Contains khong yeu cau cau truc cau hoi: khang dinh "khong ton
    # xang" van khop (gioi han da biet — dap an generic nen vo hai).
    assert match_faq("xe này không tốn xăng đâu", bank)["id"] == "fuel-generic"
    # Cau khang dinh khong co "khong" thi khong khop pattern hoi.
    assert decide_gate("xe này tốn xăng quá", False, False,
                        bank).action == "pipeline"
    assert decide_gate("Em ở Hà Nội", False, False, bank).action == "pipeline"


def test_decide_default_bank():
    d = decide_gate("Chào bác", has_pending=False, has_lead=False)
    assert d.action == "faq" and d.faq_id == "greeting"


# ---------- tich hop: gate chan truoc provider ----------

def test_bypass_turn_skips_provider_and_creates_no_persona_message(session):
    pipe = Pipeline(session, _NoCallProvider(), FakeSender())
    r = pipe.run(_req("👍"))
    assert r.status == "skipped"
    assert r.reply_text is None and r.message_id is None
    assert "không gọi LLM" in r.flow[0]
    msgs = session.query(Message).all()
    assert len(msgs) == 1 and msgs[0].role == "user"  # chi comment khach


def test_faq_turn_sends_canned_answer_without_provider(session):
    pipe = Pipeline(session, _NoCallProvider(), FakeSender())
    r = pipe.run(_req("Bác đi Creta tốn xăng không?"))
    assert r.status == "sent"
    assert r.reply_text == ("Mức tốn xăng còn tùy đường đi và cách chạy "
                            "nên mình không dám phán chắc con số cụ thể.")
    msg = session.get(Message, r.message_id)
    assert msg is not None and msg.status == "sent"
    assert session.query(Delivery).filter_by(
        message_id=r.message_id).count() == 1


def test_pending_question_forces_pipeline(session):
    first = turn(Pipeline(session, FakeProvider(), FakeSender()),
                 "Em đang phân vân.")
    session.add(QuestionState(thread_id=first.thread_id, actor_id="actor-1",
                              question_key="area",
                              question_text="Bạn ở khu vực nào?",
                              status="pending"))
    session.commit()
    counting = _CountingProvider(FakeProvider())
    pipe = Pipeline(session, counting, FakeSender())
    # Câu có nội dung thật vẫn vào pipeline (có thể là đáp án).
    r = pipe.run(_req("Em ở Hà Nội", thread_id=first.thread_id))
    assert counting.calls >= 1
    assert r.status != "skipped"


def test_ack_skips_open_pending_question_but_answers_yes_no(session):
    first = turn(Pipeline(session, FakeProvider(), FakeSender()),
                 "Em đang phân vân.")
    session.add(QuestionState(thread_id=first.thread_id, actor_id="actor-1",
                              question_key="area",
                              question_text="Bạn ở khu vực nào?",
                              status="pending"))
    session.commit()
    counting = _CountingProvider(FakeProvider())
    pipe = Pipeline(session, counting, FakeSender())
    # 'ok' không trả lời được câu hỏi mở -> bypass, không gọi LLM.
    r = pipe.run(_req("ok", thread_id=first.thread_id))
    assert counting.calls == 0
    assert r.status == "skipped"
    # Đổi câu hỏi chờ sang dạng có/không thì 'ok' là đáp án -> pipeline.
    session.query(QuestionState).filter_by(
        thread_id=first.thread_id).update(
        {"question_text": "Cậu có muốn tham khảo sale không?"})
    session.commit()
    counting2 = _CountingProvider(FakeProvider())
    pipe2 = Pipeline(session, counting2, FakeSender())
    r2 = pipe2.run(_req("ok", thread_id=first.thread_id))
    assert counting2.calls >= 1
    assert r2.status != "skipped"


def test_lead_signal_forces_pipeline(session):
    counting = _CountingProvider(FakeProvider())
    pipe = Pipeline(session, counting, FakeSender())
    r = pipe.run(_req("Em tính mua Creta"))
    assert counting.calls >= 1
    assert r.status != "skipped"


def test_bypass_idempotent_replay_saves_no_duplicate_user_message(session):
    pipe = Pipeline(session, _NoCallProvider(), FakeSender())
    r1 = pipe.run(_req("👍"), idempotency_key="k-skip")
    r2 = pipe.run(_req("👍", thread_id=r1.thread_id), idempotency_key="k-skip")
    assert r1.status == "skipped" and r2.status == "skipped"
    assert session.query(Message).filter_by(role="user").count() == 1


def test_faq_idempotent_replay_returns_same_message(session):
    pipe = Pipeline(session, _NoCallProvider(), FakeSender())
    r1 = pipe.run(_req("Chào bác"), idempotency_key="k-faq")
    r2 = pipe.run(_req("Chào bác", thread_id=r1.thread_id),
                   idempotency_key="k-faq")
    assert r1.status == "sent" and r2.status == "sent"
    assert r1.message_id == r2.message_id
    assert session.query(Message).filter_by(role="persona").count() == 1
