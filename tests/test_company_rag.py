"""RAG company (đại lý) + luồng lead: chunk tách khỏi persona, retrieval
theo tỉnh, validator kind company, prompt LEAD_CAPTURE, cổng lead."""
import json
import re
from pathlib import Path

from app.engine.embeddings import TfidfEmbedder
from app.engine.rag import (build_company_chunks, has_lead_signal,
                            retrieve_company_chunks)
from app.engine.runtime_prompt import CONVERSATION_POLICY
from app.engine.validator import validate
from app.models import RagChunk
from app.providers.openrouter import SYSTEM_PROMPT

ROOT = Path(__file__).resolve().parents[1]
PHONE_RE = re.compile(r"0\d{8,}")

DEALERS = [
    {"code": "VN029", "name": "Hyundai Đông Đô", "short": "HDDO",
     "region": "HN", "province": "Hà Nội",
     "address": "Số 987 Tam Trinh, phường Hoàng Mai, thành phố Hà Nội"},
    {"code": "VC004", "name": "Hyundai Vinh", "short": "HVIN",
     "region": "Trung", "province": "Nghệ An",
     "address": "Km 3 + 500, đại lộ Lê Nin, xã Nghi Phú, TP Vinh, Nghệ An"},
    {"code": "VS034", "name": "Hyundai Việt Hàn", "short": "HVHA",
     "region": "HCM", "province": "TP Hồ Chí Minh",
     "address": "Số 387 Quốc Lộ 13, Phường Hiệp Bình, TP. HCM"},
]


def test_build_company_chunks_shape_and_no_pii():
    chunks = build_company_chunks(DEALERS)
    assert [c["id"] for c in chunks] == [
        "company_VN029", "company_VC004", "company_VS034"]
    assert all(c["domain"] == "company" and c["persona_id"] is None
               for c in chunks)
    assert "Hà Nội" in chunks[0]["text"] and "Tam Trinh" in chunks[0]["text"]
    # Toàn bộ file dealers thật đã làm sạch: không SĐT, không email.
    payload = json.loads((ROOT / "data" / "dealers.json").read_text())
    assert len(payload["dealers"]) == 59
    for c in build_company_chunks(payload["dealers"]):
        assert not PHONE_RE.search(c["text"]), c["id"]
        assert "@" not in c["text"], c["id"]


def test_retrieve_company_chunks_by_province(session):
    chunks = build_company_chunks(DEALERS)
    emb = TfidfEmbedder().fit([c["text"] for c in chunks])
    for c, vec in zip(chunks, emb.embed([c["text"] for c in chunks])):
        session.add(RagChunk(id=c["id"], persona_id=None, domain="company",
                             chunk_type=c["chunk_type"],
                             source_ref=c["source_ref"], text=c["text"],
                             embedding=json.dumps(vec)))
    session.commit()
    qv = emb.embed(["đại lý Hyundai ở Hà Nội"])[0]
    top = retrieve_company_chunks(session, qv, limit=3, threshold=0.10)
    assert top and top[0]["chunk_id"] == "company_VN029"


def test_validator_company_claim():
    chunk = ("Đại lý: Hyundai Đông Đô (VN029). Khu vực: HN. "
             "Tỉnh thành: Hà Nội. Địa chỉ: Số 987 Tam Trinh, phường "
             "Hoàng Mai, thành phố Hà Nội.")
    retrieved = {"company_VN029": ("company", chunk, {"status": "active"})}
    ok = validate(
        {"text": "Bạn qua Hyundai Đông Đô ở Tam Trinh xem xe nhé.",
         "claims": [{"kind": "company", "ref_id": "company_VN029",
                     "value": "Hyundai Đông Đô"}]},
        {"retrieved": retrieved})
    assert not [e for e in ok if e.startswith("company_")]
    bad = validate(
        {"text": "Bạn qua Hyundai Vinh ở Nghệ An xem xe nhé.",
         "claims": [{"kind": "company", "ref_id": "company_VN029",
                     "value": "Hyundai Vinh ở Nghệ An"}]},
        {"retrieved": retrieved})
    assert any(e.startswith("company_unsaved_detail") for e in bad)


def test_validator_phone_ask_allowed_only_in_lead_flow():
    final = {"text": "Bạn có tiện cho mình số điện thoại để gửi sale "
                     "add Zalo tư vấn không?"}
    assert any(e.startswith("private_request")
               for e in validate(final, {"retrieved": {}}))
    assert not validate(final, {"retrieved": {}, "lead_capture": True})


def test_prompt_lead_policy_and_no_volunteer_simulation_note():
    assert "LEAD_CAPTURE" in CONVERSATION_POLICY
    assert "TUYỆT ĐỐI KHÔNG tự chèn ghi chú" in CONVERSATION_POLICY
    assert "không xin số điện thoại/địa chỉ nhà" not in CONVERSATION_POLICY
    assert "TUYỆT ĐỐI KHÔNG tự chèn ghi chú kiểu" in SYSTEM_PROMPT
    assert "không xin số điện thoại." not in SYSTEM_PROMPT


def test_has_lead_signal():
    assert has_lead_signal("mình đang tính mua Creta")
    assert has_lead_signal("gần mình có đại lý Hyundai nào không?")
    assert has_lead_signal("cho mình liên hệ sale")
    assert not has_lead_signal("trời mưa đường ngập thì sao?")
    assert not has_lead_signal("mình ở Hà Nội")
