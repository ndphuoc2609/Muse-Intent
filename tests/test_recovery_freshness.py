"""Retry phải kiểm tra lại bối cảnh và độ tươi của product facts."""
from sqlalchemy.orm import sessionmaker

from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.models import ProductFact
from app.providers.fake import FakeProvider
from conftest import turn


class NoCallsProvider:
    def chat(self, messages, tools):
        raise AssertionError("retry không được gọi model cho bản nháp cũ")


def new_session(session):
    return sessionmaker(bind=session.get_bind())()


def test_faq_retry_is_blocked_after_new_message(session):
    first = turn(Pipeline(session, NoCallsProvider(), FakeSender(True)),
                 "Cảm ơn bác", idem="faq-stale")
    assert first.status == "error" and first.reply_text
    later = turn(Pipeline(session, FakeProvider(), FakeSender()), "ok",
                 thread_id=first.thread_id)
    assert later.status == "skipped"
    with new_session(session) as retry_session:
        sender = FakeSender()
        result = turn(Pipeline(retry_session, NoCallsProvider(), sender),
                      "Cảm ơn bác", thread_id=first.thread_id, idem="faq-stale")
        assert result.status in ("error", "review")
        assert sender.sent == []


def test_product_expires_between_failed_delivery_and_retry(session, monkeypatch):
    from app.engine import tools

    monkeypatch.setattr(tools, "_today", lambda: "2026-12-30")
    text = "Giá Creta bản cao nhất bao nhiêu?"
    first = turn(Pipeline(session, FakeProvider(), FakeSender(True)), text,
                 idem="product-expired")
    assert first.status == "error" and "599" in (first.reply_text or "")
    monkeypatch.setattr(tools, "_today", lambda: "2027-01-01")
    with new_session(session) as retry_session:
        sender = FakeSender()
        result = turn(Pipeline(retry_session, NoCallsProvider(), sender), text,
                      thread_id=first.thread_id, idem="product-expired")
        assert result.status in ("error", "review")
        assert sender.sent == []


def test_new_conflicting_product_fact_blocks_retry(session, monkeypatch):
    from app.engine import tools

    monkeypatch.setattr(tools, "_today", lambda: "2026-12-30")
    text = "Giá Creta bản cao nhất bao nhiêu?"
    first = turn(Pipeline(session, FakeProvider(), FakeSender(True)), text,
                 idem="product-conflict")
    assert first.status == "error" and "599" in (first.reply_text or "")
    existing = session.query(ProductFact).filter_by(model="Hyundai Creta",
                                                    fact_type="price").first()
    session.add(ProductFact(model=existing.model, trim=existing.trim,
                            fact_type=existing.fact_type, value_text="699 triệu đồng",
                            value_number=699, unit="triệu đồng", source="sample",
                            status="active", is_sample=True, valid_to="2026-12-31"))
    session.commit()
    with new_session(session) as retry_session:
        sender = FakeSender()
        result = turn(Pipeline(retry_session, NoCallsProvider(), sender), text,
                      thread_id=first.thread_id, idem="product-conflict")
        assert result.status in ("error", "review")
        assert sender.sent == []
