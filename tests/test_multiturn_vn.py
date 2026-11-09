"""Hội thoại nhiều lượt tiếng Việt (Phase 3d): đổi mẫu xe, nhiều
người bình luận, khách đính chính, giá thiếu/hết hạn, gửi lỗi rồi
lượt mới. FakeProvider + SQLite in-memory, KHÔNG gọi OpenRouter."""
from sqlalchemy import select

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.models import CustomerFact, Message, ProductFact, QuestionState
from app.providers.fake import FakeProvider
from conftest import turn
from test_delivery_recovery import CorrectionProvider


def _product_claim(session, result):
    claims = (result.trace or {}).get("delivery_recovery", {}).get(
        "final", {}).get("claims", [])
    products = [c for c in claims if c.get("kind") == "product"]
    assert len(products) == 1, claims
    return session.get(ProductFact, products[0]["ref_id"])


def test_model_switching_keeps_each_thread_on_its_own_model(pipe, session):
    # Thread Creta: hỏi "xe này" -> giá Creta (mẫu).
    r_creta = turn(pipe, "xe này giá bao nhiêu?")
    assert r_creta.status == "sent"
    assert "599" in r_creta.reply_text
    assert _product_claim(session, r_creta).model == "Hyundai Creta"
    # Thread Tucson (persona khác): hỏi "xe này" -> giá Tucson (mẫu).
    r_tucson = turn(pipe, "xe này giá bao nhiêu?", post="Bài về Tucson.",
                    vehicles=["Hyundai Tucson"], persona_id="AUTO_089")
    assert r_tucson.status == "sent"
    assert "769" in r_tucson.reply_text
    assert _product_claim(session, r_tucson).model == "Hyundai Tucson"
    # Trong thread Creta, hỏi sang Tucson rồi hỏi lại xe mình: canon
    # không đổi — persona vẫn đi Creta, không nhận Tucson làm xe mình.
    r_other = turn(pipe, "Còn Tucson thì sao bác?",
                   thread_id=r_creta.thread_id)
    assert r_other.status == "sent"
    assert "Tucson" not in (r_other.reply_text or "")
    r_mine = turn(pipe, "Bác đang đi xe gì?", thread_id=r_creta.thread_id)
    assert r_mine.status == "sent"
    assert "Hyundai Creta" in r_mine.reply_text


def test_multi_person_comments_do_not_share_pending_questions(pipe, session):
    r1 = turn(pipe, "Em đang cân nhắc con này cho mẹ.")
    assert r1.status == "sent"
    qs = session.execute(
        select(QuestionState).where(
            QuestionState.thread_id == r1.thread_id,
            QuestionState.question_key == "like_point")).scalars().all()
    assert len(qs) == 1 and qs[0].actor_id == "actor-1"
    assert qs[0].status == "pending"
    # Actor-2 nói "ok": không trả lời câu hỏi của actor-1, lượt bị bỏ qua.
    r2 = turn(pipe, "ok", thread_id=r1.thread_id, actor="actor-2")
    assert r2.status == "skipped"
    assert session.get(QuestionState, qs[0].id).status == "pending"
    assert session.query(QuestionState).filter_by(
        thread_id=r1.thread_id, actor_id="actor-2").count() == 0
    # Actor-1 trả lời đúng câu hỏi của mình thì mới đánh dấu answered.
    r3 = turn(pipe, "Thích vì rộng rãi", thread_id=r1.thread_id)
    assert r3.status == "sent"
    assert session.get(QuestionState, qs[0].id).status == "answered"


def test_customer_correction_supersedes_previous_fact(session):
    pipe = Pipeline(session, FakeProvider(), FakeSender())
    r1 = turn(pipe, "Em đang cân nhắc con này cho mẹ.")
    assert r1.status == "sent"
    mom = session.query(CustomerFact).filter_by(
        thread_id=r1.thread_id, actor_id="actor-1",
        predicate="intended_user", status="current").one()
    assert mom.relation == "mẹ"
    pipe2 = Pipeline(session, CorrectionProvider(), FakeSender())
    r2 = turn(pipe2, "À không, mua cho mình", thread_id=r1.thread_id)
    assert r2.status == "sent"
    assert session.get(CustomerFact, mom.id).status == "corrected"
    mine = session.query(CustomerFact).filter_by(
        thread_id=r1.thread_id, actor_id="actor-1",
        predicate="intended_user", status="current").one()
    assert mine.value_raw == "mình"
    assert session.query(CustomerFact).filter_by(
        thread_id=r1.thread_id, actor_id="actor-1",
        predicate="intended_user", status="current").count() == 1


def test_missing_price_is_not_invented(pipe, session):
    r = turn(pipe, "xe này giá bao nhiêu?", post="Bài về Santa Fe.",
             vehicles=["Hyundai Santa Fe"])
    assert r.status == "sent"
    assert "không có dữ kiện" in r.reply_text
    assert "triệu" not in r.reply_text
    claims = (r.trace or {}).get("delivery_recovery", {}).get(
        "final", {}).get("claims", [])
    assert not [c for c in claims if c.get("kind") == "product"]


def test_stale_price_is_not_shown(pipe, session):
    session.add(ProductFact(
        model="Hyundai Santa Fe", trim="2.5", fact_type="price",
        value_text="999 triệu đồng", value_number=999, unit="triệu đồng",
        source="test", valid_from="2026-01-01", valid_to="2026-11-01",
        is_sample=False))
    session.commit()
    r = turn(pipe, "xe này giá bao nhiêu?", post="Bài về Santa Fe.",
             vehicles=["Hyundai Santa Fe"])
    assert r.status == "sent"
    # Giá hết hạn bị lọc ở retrieval: không hiện số cũ, không bịa số mới.
    assert "999" not in r.reply_text
    assert "không có dữ kiện" in r.reply_text


def test_failed_send_does_not_block_next_turn(session):
    sender = FakeSender(fail_next=True)
    pipe = Pipeline(session, FakeProvider(), sender)
    r1 = turn(pipe, "Bác đang đi xe gì?", idem="draft-fail")
    assert r1.status == "error"
    assert sender.sent == []
    assert session.get(Message, r1.message_id).status == "error"
    # Lượt mới (không idem cũ): gửi bình thường, draft lỗi giữ nguyên.
    pipe2 = Pipeline(session, FakeProvider(), FakeSender())
    r2 = turn(pipe2, "Bác mua xe khi nào?", thread_id=r1.thread_id)
    assert r2.status == "sent"
    assert session.get(Message, r1.message_id).status == "error"
    assert session.get(Message, r2.message_id).status == "sent"
