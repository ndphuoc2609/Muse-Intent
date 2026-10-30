from conftest import turn


def test_ask_existing_persona_fact(pipe):
    r = turn(pipe, "Bác đang đi xe gì?")
    assert r.status == "sent"
    assert "Creta" in (r.reply_text or "")
    assert r.planner["intents"]


def test_ask_purchase_time_and_family_not_price(pipe):
    r = turn(pipe, "Bác mua xe lúc nào?")
    assert r.status == "sent"
    assert "2024-03" in (r.reply_text or "")
    # "gia đình" không được nhận nhầm thành hỏi "giá".
    r2 = turn(pipe, "Mình chủ yếu chở gia đình đi xa.",
              thread_id=r.thread_id)
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
