from app.engine.tools import ToolExecutor
from app.models import ProductFact
from conftest import turn


def test_valid_product_fact(pipe):
    r = turn(pipe, "Giá Creta bản cao nhất bao nhiêu?")
    assert r.status == "sent"
    assert "599" in (r.reply_text or "")


def test_expired_fact_not_used(pipe, session):
    # Hết hạn: đổi valid_to về quá khứ, tool không trả active nữa.
    f = session.query(ProductFact).filter_by(fact_type="price").first()
    f.valid_to = "2020-01-01"
    session.commit()
    r = turn(pipe, "Giá Creta bản cao nhất bao nhiêu?")
    assert "599" not in (r.reply_text or "")


def test_wrong_model_not_returned(pipe, session):
    ex = ToolExecutor(session, "hoang-nam", "t", "actor-1")
    r = ex.execute("get_product_facts", {"model": "Hyundai Tucson"})
    assert r.status == "NOT_FOUND"


def test_conflicting_product_facts(pipe, session):
    session.add(ProductFact(model="Hyundai Creta", trim="1.5 Cao cấp",
                            fact_type="price", value_text="699 triệu đồng",
                            value_number=699, unit="triệu đồng",
                            source="sample", is_sample=True))
    session.commit()
    ex = ToolExecutor(session, "hoang-nam", "t", "actor-1")
    r = ex.execute("get_product_facts",
                   {"model": "Hyundai Creta", "fact_type": "price"})
    assert r.status == "CONFLICT"


def test_no_source_no_claim(pipe, session):
    session.query(ProductFact).delete()
    session.commit()
    r = turn(pipe, "Giá Creta bao nhiêu?")
    assert r.status == "sent"
    assert "không có dữ kiện" in (r.reply_text or "")
