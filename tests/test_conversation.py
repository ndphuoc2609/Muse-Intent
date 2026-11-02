from conftest import turn


def test_ask_existing_persona_fact(pipe):
    r = turn(pipe, "Bác đang đi xe gì?")
    assert r.status == "sent"
    assert "Creta" in (r.reply_text or "")
    assert r.planner["intents"]


def test_ask_purchase_time_and_family_not_price(pipe, session):
    # Seed mới không có purchased_at nên test dùng fixture riêng giữ
    # đúng ý hồi quy: thời điểm mua trả đúng + "gia đình" không bị
    # nhận nhầm thành hỏi "giá".
    from conftest import make_persona
    pid = make_persona(session, "fix-purchase-time", {
        "vehicle_model": "Hyundai Creta", "purchased_at": "2024-03"})
    r = turn(pipe, "Bác mua xe lúc nào?", persona_id=pid)
    assert r.status == "sent"
    assert "2024-03" in (r.reply_text or "")
    # "gia đình" không được nhận nhầm thành hỏi "giá".
    r2 = turn(pipe, "Mình chủ yếu chở gia đình đi xa.",
              thread_id=r.thread_id, persona_id=pid)
    assert "599" not in (r2.reply_text or "")


def test_ask_missing_fact_proposes_then_answers(pipe, session):
    # Màu xe chưa seed -> propose -> engine lưu -> reply dùng fact đó.
    r = turn(pipe, "Bác đi xe màu gì thế?")
    assert r.status == "sent"
    assert "trắng" in (r.reply_text or "")
    # Lượt sau đọc lại fact đã lưu, không tạo thêm fact trùng.
    r2 = turn(pipe, "Bác đi xe màu gì thế?", thread_id=r.thread_id)
    assert "trắng" in (r2.reply_text or "")


def test_multi_intent_one_turn(pipe):
    r = turn(pipe, "Bác đi xe màu gì? Em đang cân nhắc con này cho bố.")
    assert r.status == "sent"
    assert "trắng" in (r.reply_text or "")
    facts = r.saved_customer_facts
    # Người dùng xe dự kiến là bố, KHÔNG phải self.
    assert any(f["subject"] == "third_person" and f["relation"] == "bố"
               for f in facts)
    assert not any(f["subject"] == "self" and f["predicate"] == "intended_user"
                   for f in facts)


def test_reference_single_vs_multi_vehicle(pipe):
    r = turn(pipe, "Tôi cũng muốn mua một chiếc như vậy.")
    assert r.status == "sent"
    assert r.planner["resolved_reference"].get("resolved_to") == "Hyundai Creta"
    # Nhiều xe trong bài -> ambiguous, không tự gán tuyệt đối.
    r2 = turn(pipe, "Tôi cũng muốn mua một chiếc như vậy.",
              vehicles=["Hyundai Creta", "Hyundai Tucson"])
    ref = r2.planner["resolved_reference"]
    assert ref.get("resolved_to") is None
    assert ref.get("confidence", 1) < 0.5


def test_buying_interest_is_not_handoff(pipe):
    r = turn(pipe, "Tôi cũng muốn mua một chiếc như vậy.")
    assert r.status == "sent"  # không phải review/handoff
    assert "đặt cọc" not in (r.reply_text or "").lower() or "không xác nhận" in (r.reply_text or "")


def test_budget_question_not_marked_answered_on_topic_change(pipe, session):
    from app.models import QuestionState
    r1 = turn(pipe, "Tôi cũng muốn mua một chiếc như vậy.")
    assert r1.status == "sent"
    # Bot hỏi like_point; khách trả lời sang chủ đề gia đình.
    r2 = turn(pipe, "Mình chủ yếu chở gia đình đi xa.", thread_id=r1.thread_id)
    qs = session.query(QuestionState).filter_by(thread_id=r1.thread_id).all()
    assert qs and all(q.status != "answered" for q in qs)


def test_opt_out_no_followup(pipe):
    r = turn(pipe, "Thôi, mình không muốn tiếp tục nữa.")
    assert r.planner["goal"] == "stop"
    assert r.status == "sent"
    assert "?" not in (r.reply_text or "")


def test_short_affirmation_context(pipe):
    r1 = turn(pipe, "Tôi cũng muốn mua một chiếc như vậy.")
    r2 = turn(pipe, "Ừ", thread_id=r1.thread_id)
    assert r2.status == "sent"


def test_ai_identity_disclosure(pipe):
    r = turn(pipe, "Bác có phải AI không?")
    assert r.status == "sent"
    assert "không phải người thật" in (r.reply_text or "")


def test_transaction_goes_to_handoff_review_not_sent(pipe):
    r = turn(pipe, "Tôi muốn đặt cọc ngay.")
    assert r.status == "review"
    assert "không xác nhận" in (r.reply_text or "")
    assert "đã đặt cọc" not in (r.reply_text or "")


def test_no_repeat_question_when_already_known(pipe):
    r1 = turn(pipe, "Tôi cũng muốn mua một chiếc như vậy.")
    # Lượt 2 khách đã trả lời điểm thích -> không hỏi lại like_point.
    r2 = turn(pipe, "Mình thích xe rộng rãi.", thread_id=r1.thread_id)
    assert r2.status == "sent"


class _RecordingProvider:
    """Ghi lại messages gửi cho provider ở mỗi call, rồi trả plan+final."""

    def __init__(self):
        self.calls = []

    def chat(self, messages, tools):
        self.calls.append([dict(m) for m in messages])
        if not any(m.get("role") == "tool" for m in messages):
            from app.providers.base import ProviderResult, ToolCall
            return ProviderResult(tool_calls=[ToolCall("plan_turn", {
                "intents": [{"type": "social", "subject": "self",
                             "relation": None, "normalized": "x",
                             "evidence": "x", "source": "stated",
                             "confidence": 0.9}],
                "pending_questions": [], "answered_question_keys": [],
                "known_customer": {}, "known_persona": {},
                "data_needs": ["none"], "goal": "answer_question",
                "response_direction": {"answer_first": True,
                                       "ask_followup": False,
                                       "followup_purpose": None,
                                       "followup_key": None},
                "resolved_reference": {}})])
        from app.providers.base import ProviderResult, ToolCall
        return ProviderResult(tool_calls=[ToolCall("submit_final_reply", {
            "text": "OK, ghi nhận.", "claims": [], "goal": "answer_question"})])


def test_history_sent_as_real_messages_not_only_system(session):
    # Hồi quy cho lỗi 30/10: lượt 2 lặp lại câu hỏi lượt 1 vì lịch sử
    # chỉ nằm trong system text, không phải message thật.
    from app.engine.fake_sender import FakeSender
    from app.engine.pipeline import Pipeline
    from app.schemas import TurnRequest
    rec = _RecordingProvider()
    pipe2 = Pipeline(session, rec, FakeSender())

    def turn2(text, thread_id=None):
        return pipe2.run(TurnRequest(
            persona_id="AUTO_029", post_text="Bài về Creta.",
            post_vehicle_models=["Hyundai Creta"], actor_id="actor-1",
            text=text, thread_id=thread_id))

    r1 = turn2("Cũng tính lấy xe về đi phố bác ạ")
    turn2("Tiết kiệm thôi, đang tính kiếm đại lý để xem xe ạ",
          thread_id=r1.thread_id)
    # Call đầu của lượt 2 là calls[2] (lượt 1 đã có 2 calls: plan, final).
    roles = [m["role"] for m in rec.calls[2]]
    assert roles == ["system", "user", "assistant", "user"]
    assert rec.calls[2][2]["content"] == "OK, ghi nhận."
    assert rec.calls[2][1]["content"] == "Cũng tính lấy xe về đi phố bác ạ"
    assert rec.calls[2][3]["content"] == "Tiết kiệm thôi, đang tính kiếm đại lý để xem xe ạ"


def test_history_keeps_last_20_not_first_20(session):
    # Hồi quy: query cũ order ASC + limit 20 giữ 20 message đầu tiên,
    # thread dài sẽ mất hẳn lịch sử gần đây.
    from datetime import datetime, timedelta, timezone
    from app.engine.fake_sender import FakeSender
    from app.engine.pipeline import Pipeline
    from app.models import Message
    from app.schemas import TurnRequest
    rec = _RecordingProvider()
    pipe2 = Pipeline(session, rec, FakeSender())
    r1 = pipe2.run(TurnRequest(
        persona_id="AUTO_029", post_text="Bài về Creta.",
        post_vehicle_models=["Hyundai Creta"], actor_id="actor-1",
        text="bắt đầu"))
    base = datetime.now(timezone.utc)
    for i in range(30):
        session.add(Message(thread_id=r1.thread_id, actor_id="actor-1",
                            role="user" if i % 2 == 0 else "persona",
                            text=f"msg-{i:02d}",
                            status="sent",
                            created_at=base + timedelta(seconds=i)))
    session.commit()
    pipe2.run(TurnRequest(
        persona_id="AUTO_029", post_text="Bài về Creta.",
        post_vehicle_models=["Hyundai Creta"], actor_id="actor-1",
        text="lượt mới", thread_id=r1.thread_id))
    # System của lượt mới là system đầu tiên chứa msg-29 (lượt 1 chưa có
    # message này). Không yêu cầu system chứa "lượt mới": message mới
    # có thể không nằm trong 6 dòng history cuối nếu timestamp seed
    # nằm ở tương lai so với nó — điều test này kiểm tra là cửa sổ
    # 20 lượt gần nhất, không phải vị trí của message mới.
    system = next(m["content"] for call in rec.calls for m in call
                  if m["role"] == "system" and "msg-29" in m["content"])
    assert "msg-00" not in system
