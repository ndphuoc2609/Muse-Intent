"""Hội thoại tự nhiên — brief ưu tiên 30/10 (§10): persona hiểu đúng
người dùng, trả lời tự nhiên, nhớ điều đã nói, khơi gợi đúng lúc.

Quy tắc test ở đây: assert THUỘC TÍNH HÀNH VI (đúng ý, đủ ý, liền
mạch, không hỏi lặp, không nhầm chủ thể, không gán suy luận, không
chặn nhầm), KHÔNG exact-match một câu trả lời mẫu. FakeProvider chỉ
dùng để kiểm tra engine; các kịch bản tinh tế dùng stub provider mô
phỏng đúng planner/tool call mà Luna thật sẽ sinh, để test tập trung
vào state/validation/delivery của engine.
"""
import json

from sqlalchemy import select

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.engine.typed_values import strip_claim_wrappers, value_matches
from app.engine.validator import validate
from app.models import CustomerFact, PersonaStory, QuestionState
from app.providers.base import ProviderResult, ToolCall
from app.schemas import TurnRequest


class _Stub:
    """Base cho stub provider: theo dõi lượt theo user text hiện tại."""

    def __init__(self):
        self._last_text = None
        self._turn = -1
        self.systems = []

    def _turn_index(self, messages):
        user_text = ""
        for m in messages:
            if m.get("role") == "user" and "VALIDATOR_FEEDBACK" not in (
                    m.get("content") or ""):
                user_text = m.get("content", "")
        if user_text != self._last_text:
            self._last_text = user_text
            self._turn += 1
            if messages and messages[0].get("role") == "system":
                self.systems.append(messages[0].get("content", ""))
        return self._turn

    def _tools(self, messages):
        out = []
        for m in messages:
            if m.get("role") == "tool":
                try:
                    out.append(json.loads(m.get("content", "{}")))
                except Exception:
                    pass
        return out

    def _has(self, messages, tool):
        return any(t.get("tool") == tool for t in self._tools(messages))

    def _data(self, messages, tool):
        for t in self._tools(messages):
            if t.get("tool") == tool:
                return t.get("data") or {}
        return {}

    def _repairing(self, messages):
        for m in messages:
            if m.get("role") == "user" and "VALIDATOR_FEEDBACK" in (
                    m.get("content") or ""):
                return m.get("content", "")
        return None


def _run(pipe, persona, text, thread_id=None, actor="actor-1",
         post="Bài về Creta.", vehicles=None):
    return pipe.run(TurnRequest(
        persona_id=persona, post_text=post,
        post_vehicle_models=vehicles or ["Hyundai Creta"],
        actor_id=actor, text=text, thread_id=thread_id))


def _pipe(session, provider):
    return Pipeline(session, provider, FakeSender())


def _plan(intents, goal="answer_question", answered=None, resolved=None):
    return {"intents": intents, "pending_questions": [],
            "answered_question_keys": answered or [],
            "known_customer": {}, "known_persona": {},
            "data_needs": [], "goal": goal,
            "response_direction": {"answer_first": True,
                                   "ask_followup": False},
            "resolved_reference": resolved or {}}


def _intent(typ, subject="self", relation=None, norm="", source="stated"):
    return {"type": typ, "subject": subject, "relation": relation,
            "normalized": norm, "evidence": norm, "source": source,
            "confidence": 0.9}


# --- 1. Hỏi hai dữ kiện trong một câu (Fake: engine proof) ---

def test_two_facts_one_turn(pipe):
    from tests.conftest import turn
    r = turn(pipe, "Bác đi xe gì và mua lúc nào?")
    assert r.status == "sent"
    assert "Creta" in (r.reply_text or "")
    assert "2024-03" in (r.reply_text or "")


# --- 2. Quan tâm mua KHÁC yêu cầu giao dịch ---

def test_interest_is_not_transaction(pipe):
    from tests.conftest import turn
    r = turn(pipe, "Tôi cũng muốn mua một chiếc như vậy.")
    assert r.status == "sent"
    assert r.planner["goal"] != "handoff"
    for p in ("đã đặt cọc", "đã giữ xe", "đã chốt xe", "nhân viên sẽ gọi"):
        assert p not in (r.reply_text or "")


# --- 3. Trả lời ngắn cho câu hỏi trước: ghi nhận, không hỏi lặp ---

def test_short_answer_marks_question_answered_no_repeat(pipe, session):
    from tests.conftest import turn
    r1 = turn(pipe, "Tôi cũng muốn mua một chiếc như vậy.")
    assert r1.status == "sent"
    pending = session.execute(
        select(QuestionState).where(
            QuestionState.thread_id == r1.thread_id,
            QuestionState.actor_id == "actor-1",
            QuestionState.status == "pending")).scalars().all()
    assert any(q.question_key == "like_point" for q in pending)
    r2 = turn(pipe, "Tiết kiệm thôi.", thread_id=r1.thread_id)
    assert r2.status == "sent"
    q = session.execute(
        select(QuestionState).where(
            QuestionState.thread_id == r1.thread_id,
            QuestionState.actor_id == "actor-1",
            QuestionState.question_key == "like_point")).scalars().first()
    assert q is not None and q.status == "answered"
    assert "thích mẫu này ở điểm nào" not in (r2.reply_text or "")


# --- 4. Chuyển chủ đề: câu hỏi cũ vẫn pending nhưng không bị hỏi lại ---

def test_topic_switch_keeps_pending_unasked(pipe, session):
    from tests.conftest import turn
    r1 = turn(pipe, "Tôi cũng muốn mua một chiếc như vậy.")
    r2 = turn(pipe, "Bác đi xe gì?", thread_id=r1.thread_id)
    assert r2.status == "sent"
    assert "Creta" in (r2.reply_text or "")
    q = session.execute(
        select(QuestionState).where(
            QuestionState.thread_id == r1.thread_id,
            QuestionState.actor_id == "actor-1",
            QuestionState.question_key == "like_point")).scalars().first()
    assert q is not None and q.status == "pending"
    assert "thích mẫu này ở điểm nào" not in (r2.reply_text or "")


# --- 5. Hỏi hộ rồi sửa chủ thể: fact cũ bị supersede ---

class _CorrectionStub(_Stub):
    def chat(self, messages, tools):
        turn = self._turn_index(messages)
        if not self._has(messages, "plan_turn"):
            if turn == 0:
                plan = _plan([_intent("third_party", "third_person", "bố",
                                      "mua cho bố")])
            else:
                plan = _plan([_intent("correction", "self", None,
                                      "sửa chủ thể thành mình")])
            return ProviderResult(tool_calls=[ToolCall("plan_turn", plan)])
        if not self._has(messages, "propose_customer_fact"):
            if turn == 0:
                call = ToolCall("propose_customer_fact", {
                    "subject": "third_person", "relation": "bố",
                    "predicate": "intended_user", "value_raw": "bố",
                    "evidence": "Mình mua cho bố.", "source": "stated"})
            else:
                call = ToolCall("propose_customer_fact", {
                    "subject": "self", "relation": None,
                    "predicate": "intended_user", "value_raw": "mình",
                    "evidence": "À không, mua cho mình.", "source": "stated",
                    "correction": True})
            return ProviderResult(tool_calls=[call])
        text = ("Ừ, mua cho bố thì cứ xem xe rộng rãi nhé." if turn == 0
                else "Vậy là mua cho bạn, để mình kể trải nghiệm đi phố nhé.")
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": text, "claims": [], "goal": "answer_question"})])


def test_third_party_then_correction_supersedes(session):
    pipe = _pipe(session, _CorrectionStub())
    r1 = _run(pipe, "hoang-nam", "Mình mua cho bố.")
    assert r1.status == "sent"
    r2 = _run(pipe, "hoang-nam", "À không, mua cho mình.",
              thread_id=r1.thread_id)
    assert r2.status == "sent"
    rows = session.execute(
        select(CustomerFact).where(
            CustomerFact.thread_id == r1.thread_id,
            CustomerFact.actor_id == "actor-1",
            CustomerFact.predicate == "intended_user")).scalars().all()
    by_status = {r.status: r for r in rows}
    assert by_status["corrected"].relation == "bố"
    assert by_status["current"].subject == "self"
    assert "bố" not in (r2.reply_text or "")


# --- 6. Chỉ xem thôi: không tạo followup, không hỏi ngân sách ---

class _BrowseOnlyStub(_Stub):
    def chat(self, messages, tools):
        self._turn_index(messages)
        if not self._has(messages, "plan_turn"):
            return ProviderResult(tool_calls=[ToolCall("plan_turn", _plan(
                [_intent("negation", "self", None, "khách chỉ xem thôi")]))])
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": "Ừ, cứ xem thoải mái nhé, khi nào cần thì nhắn mình.",
            "claims": [], "goal": "answer_question"})])


def test_browse_only_creates_no_followup(session):
    pipe = _pipe(session, _BrowseOnlyStub())
    r = _run(pipe, "hoang-nam", "Mình chỉ xem thôi.")
    assert r.status == "sent"
    rows = session.execute(
        select(QuestionState).where(
            QuestionState.thread_id == r.thread_id,
            QuestionState.actor_id == "actor-1")).scalars().all()
    assert rows == []
    assert "ngân sách" not in (r.reply_text or "")
    assert "khi nào mua" not in (r.reply_text or "")


# --- 7. Hỏi lại story: dùng lại story cũ, không tạo story mới ---

class _RetellStub(_Stub):
    def chat(self, messages, tools):
        if not self._has(messages, "plan_turn"):
            return ProviderResult(tool_calls=[ToolCall("plan_turn", _plan(
                [_intent("ask_persona", "persona", None, "hỏi chuyến đi")]))])
        if not self._has(messages, "get_persona_stories"):
            return ProviderResult(tool_calls=[
                ToolCall("get_persona_stories", {})])
        stories = self._data(messages, "get_persona_stories") or []
        st = (stories if isinstance(stories, list) else []) or [{}]
        st = st[0] if st else {}
        summary = st.get("summary", "chuyến Vũng Tàu")
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": summary, "claims": [{"kind": "story",
                                         "ref_id": st.get("event_id"),
                                         "value": summary}],
            "goal": "answer_question"})])


def test_retell_uses_same_story_no_new_row(session):
    pipe = _pipe(session, _RetellStub())
    r1 = _run(pipe, "minh-anh", "Đi những đâu rồi")
    assert r1.status == "sent"
    assert "Vũng Tàu" in (r1.reply_text or "")
    r2 = _run(pipe, "minh-anh", "Kể lại chuyến đó đi",
              thread_id=r1.thread_id)
    assert r2.status == "sent"
    stories = session.execute(
        select(PersonaStory).where(
            PersonaStory.persona_id == "minh-anh")).scalars().all()
    assert len(stories) == 1
    assert stories[0].event_id == "evt_anh_vungtau"
    final_call = [c for c in r2.tool_calls
                  if c["tool"] == "submit_final_reply"][-1]
    assert final_call["arguments"]["claims"][0]["ref_id"] == "evt_anh_vungtau"


# --- 8. Hai actor xen kẽ: state không lẫn ---

def test_two_actors_isolated(pipe, session):
    from tests.conftest import turn
    r1 = turn(pipe, "Tôi cũng muốn mua một chiếc như vậy.", actor="actor-1")
    assert r1.status == "sent"
    r2 = turn(pipe, "Bác đi xe gì?", thread_id=r1.thread_id, actor="actor-2")
    assert r2.status == "sent"
    q1 = session.execute(
        select(QuestionState).where(
            QuestionState.thread_id == r1.thread_id,
            QuestionState.actor_id == "actor-1",
            QuestionState.question_key == "like_point")).scalars().first()
    assert q1 is not None and q1.status == "pending"
    q2 = session.execute(
        select(QuestionState).where(
            QuestionState.thread_id == r1.thread_id,
            QuestionState.actor_id == "actor-2",
            QuestionState.question_key == "like_point")).scalars().first()
    assert q2 is None
    assert "thích mẫu này ở điểm nào" not in (r2.reply_text or "")


# --- 9. Hội thoại dài: canon vẫn trả lời đúng dù history bị cắt ---

def test_long_thread_canon_recall(pipe):
    from tests.conftest import turn
    r = turn(pipe, "Chào bạn.")
    for _ in range(12):
        r = turn(pipe, "Chào bạn.", thread_id=r.thread_id)
    r = turn(pipe, "Bác đi xe gì?", thread_id=r.thread_id)
    assert r.status == "sent"
    assert "Creta" in (r.reply_text or "")


# --- 10. Trả lời từng phần: thiếu product fact không chặn cả lượt ---

def test_partial_answer_missing_product(pipe):
    from tests.conftest import turn
    r = turn(pipe, "Bác đi xe gì và con này giá bao nhiêu?",
             vehicles=["Hyundai Santa Fe"])
    assert r.status == "sent"
    assert "Creta" in (r.reply_text or "")
    assert "không có dữ kiện" in (r.reply_text or "")
    assert "599" not in (r.reply_text or "")


# --- 11. Validator: claim tự nhiên khớp fact chuẩn, policy chặt còn nguyên ---

def test_validator_wrapper_tolerance():
    assert value_matches("Đang đi Hyundai Creta", "Hyundai Creta")
    assert value_matches("Xe của mình màu trắng", "Trắng")
    assert value_matches("Bản 1.5 Cao cấp", "1.5 Cao cấp")
    assert value_matches("Đây là xe đầu đời", "xe đầu đời")
    assert strip_claim_wrappers("Xe mình màu trắng nhé") == "trắng"
    # Policy chặt KHÔNG bị nới lỏng:
    assert not value_matches("trắng sữa", "trắng")
    assert not value_matches("Creta", "Creta 1.5")
    assert not value_matches("5999 triệu", "599 triệu")
    assert not value_matches("Hyundai Tucson", "Hyundai Creta")
    assert not value_matches("599 triệu", "769 triệu")


def test_color_claim_via_ref_id_covers():
    retrieved = {"f1": ("persona", "Trắng",
                         {"fact_key": "vehicle_color", "status": "active"})}
    errors = validate(
        {"text": "Xe mình màu trắng nhé.",
         "claims": [{"kind": "persona", "ref_id": "f1",
                     "value": "Xe của mình màu trắng"}],
         "goal": "answer_question"},
        {"retrieved": retrieved, "opted_out": False,
         "asked_ai_identity": False, "refused_keys": []})
    assert errors == []


# --- 12. Repair giữ nguyên ý định, không thay bằng câu chung chung ---

class _RepairStub(_Stub):
    """Lần đầu cố tình thiếu nhãn mẫu -> validator bắt -> repair phải
    giữ nguyên câu trả lời, chỉ thêm nhãn mẫu. Dùng fact Testcar do
    test tự seed (value_text KHÔNG tự chứa nhãn mẫu như fact Creta
    trong seed chung, vốn đã ghi '(giá mẫu...)' sẵn trong value_text)."""

    def __init__(self):
        super().__init__()
        self.repair_messages = []
        self.initial_text = None

    def _product(self, messages):
        products = self._data(messages, "get_product_facts") or []
        p = (products if isinstance(products, list) else []) or [{}]
        return p[0] if p else {}

    def chat(self, messages, tools):
        feedback = self._repairing(messages)
        if feedback is not None:
            self.repair_messages.append(feedback)
            # Repair phải dùng đúng ref_id của fact đã retrieval ở lượt
            # gốc (repair round không chạy lại executor).
            p = self._product(messages)
            return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
                "text": (self.initial_text or "") + " (dữ liệu mẫu, "
                        "không phải giá thực tế).",
                "claims": [{"kind": "product", "ref_id": p.get("id"),
                            "value": p.get("value_text", "")}],
                "goal": "answer_question"})])
        if not self._has(messages, "plan_turn"):
            return ProviderResult(tool_calls=[ToolCall("plan_turn", _plan(
                [_intent("ask_product", "self", None, "hỏi giá")]))])
        if not self._has(messages, "get_product_facts"):
            return ProviderResult(tool_calls=[
                ToolCall("get_product_facts", {"model": "Hyundai Testcar"})])
        p = self._product(messages)
        self.initial_text = f"Giá là {p.get('value_text', '')}."
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": self.initial_text,
            "claims": [{"kind": "product", "ref_id": p.get("id"),
                        "value": p.get("value_text", "")}],
            "goal": "answer_question"})])


def test_repair_preserves_intent(session):
    from app.models import ProductFact
    session.add(ProductFact(model="Hyundai Testcar", fact_type="price",
                            value_text="500 triệu đồng", is_sample=True,
                            status="active"))
    session.commit()
    stub = _RepairStub()
    pipe = _pipe(session, stub)
    r = _run(pipe, "hoang-nam", "Testcar giá bao nhiêu?")
    assert r.status == "sent"
    assert "500" in (r.reply_text or "")
    assert "mẫu" in (r.reply_text or "")
    assert "Giá là 500 triệu" in (r.reply_text or "")
    assert stub.repair_messages, "repair vòng phải chạy"
    assert "Giá là 500 triệu" in stub.repair_messages[0]


# --- 13. Context hiện câu hỏi ĐÃ TRẢ LỜI để model không hỏi lại ---

def test_answered_questions_visible_in_context(pipe, session):
    from tests.conftest import turn
    r1 = turn(pipe, "Tôi cũng muốn mua một chiếc như vậy.")
    turn(pipe, "Tiết kiệm thôi.", thread_id=r1.thread_id)
    stub = _BrowseOnlyStub()
    pipe2 = Pipeline(session, stub, FakeSender())
    r3 = _run(pipe2, "hoang-nam", "Cảm ơn bạn nhé.", thread_id=r1.thread_id)
    assert r3.status == "sent"
    assert stub.systems, "stub phải thấy system prompt"
    assert "ĐÃ TRẢ LỜI" in stub.systems[-1]
    assert "like_point" in stub.systems[-1]
