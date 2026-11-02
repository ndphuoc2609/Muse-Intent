"""RAG persona-only: chunking, TF-IDF, retrieval persona-scoped, claim kind rag."""
import json
import sqlite3
from pathlib import Path

from app.engine.embeddings import TfidfEmbedder
from app.engine.fake_sender import FakeSender
from app.engine.pipeline import Pipeline
from app.engine.rag import (build_chunks, cosine, load_index_meta,
                            retrieve_chunks)
from app.engine.validator import validate
from app.models import RagChunk, RagIndexMeta
from app.providers.fake import FakeProvider
from app.schemas import Claim, TurnRequest
from app.seed import seed

ROOT = Path(__file__).resolve().parents[1]


def _record(code: str) -> dict:
    payload = json.loads((ROOT / "data" / "personas.json").read_text())
    return next(p for p in payload["personas"] if p["code"] == code)


def test_build_chunks_types_and_exclusions():
    chunks = build_chunks(_record("AUTO_029"))
    by_type = {}
    for c in chunks:
        by_type.setdefault(c["chunk_type"], []).append(c)
    assert set(by_type) == {"memory", "perspective", "profile", "post"}
    assert len(by_type["memory"]) == 6
    assert by_type["perspective"][0]["id"] == "AUTO_029_rag_perspective"
    profile = by_type["profile"][0]["text"]
    assert "Nghề nghiệp:" in profile
    # Không có chunk loại role/trait/tag/category (no-role: nhãn
    # archetype không phải lời persona nói nên không đưa vào RAG).
    assert not (set(by_type) & {"role", "trait", "tag", "category"})
    assert all(c["id"].startswith("AUTO_029_rag_") for c in chunks)


def test_tfidf_diacritic_free_query_matches_accented_chunk():
    texts = [
        "Trời mưa to đường ngập lụt, xe có nguy cơ chết máy.",
        "Giá xăng tăng cao làm chi phí nuôi xe hàng tháng đội lên.",
    ]
    emb = TfidfEmbedder().fit(texts)
    q = emb.embed(["duong ngap nuoc khong dau"])[0]
    v = emb.embed(texts)
    assert cosine(q, v[0]) > cosine(q, v[1])
    assert cosine(v[0], v[0]) == 1.0


def _index_small(session, code: str):
    chunks = build_chunks(_record(code))
    emb = TfidfEmbedder().fit([c["text"] for c in chunks])
    for c, vec in zip(chunks, emb.embed([c["text"] for c in chunks])):
        session.add(RagChunk(id=c["id"], persona_id=c["persona_id"],
                             chunk_type=c["chunk_type"],
                             source_ref=c["source_ref"], text=c["text"],
                             embedding=json.dumps(vec)))
    for key, value in {"provider": "tfidf-v1", "threshold": 0.15,
                       "state": emb.state()}.items():
        session.add(RagIndexMeta(key=key, value=json.dumps(value)))
    session.commit()
    return emb


def test_retrieve_persona_isolation_threshold_limit(session):
    emb_a = _index_small(session, "AUTO_029")
    # Thêm chunk của persona khác với text gần giống — retrieval của
    # AUTO_029 không được trả chunk của AUTO_089.
    chunks_b = build_chunks(_record("AUTO_089"))
    emb_b = TfidfEmbedder().fit([c["text"] for c in chunks_b])
    for c, vec in zip(chunks_b, emb_b.embed([c["text"] for c in chunks_b])):
        session.add(RagChunk(id=c["id"], persona_id=c["persona_id"],
                             chunk_type=c["chunk_type"],
                             source_ref=c["source_ref"], text=c["text"],
                             embedding=json.dumps(vec)))
    session.commit()
    q = emb_a.embed(["đường ngập nước chết máy"])[0]
    got = retrieve_chunks(session, "AUTO_029", q, limit=3, threshold=0.15)
    assert got and all(g["chunk_id"].startswith("AUTO_029_rag_")
                       for g in got)
    assert len(got) <= 3
    assert all(g["score"] >= 0.15 for g in got)
    # Ngưỡng cao cắt hết.
    assert retrieve_chunks(session, "AUTO_029", q, threshold=0.999) == []


def test_validator_rag_claim():
    retrieved = {"AUTO_029_rag_mem_1": (
        "rag", "Thường chở 2 chú chó Golden đi dạo công viên cuối tuần",
        {"status": "active"})}
    ok = validate({"text": "Mình hay chở chó Golden đi dạo công viên.",
                   "claims": [{"kind": "rag",
                               "ref_id": "AUTO_029_rag_mem_1",
                               "value": "chở chó Golden đi dạo công viên"}],
                   "goal": "share_experience"},
                  {"retrieved": retrieved})
    assert not any("rag_" in e for e in ok)
    bad = validate({"text": "Mình hay đi phượt Đà Lạt mỗi tháng.",
                    "claims": [{"kind": "rag",
                                "ref_id": "AUTO_029_rag_mem_1",
                                "value": "đi phượt Đà Lạt mỗi tháng"}],
                    "goal": "share_experience"},
                   {"retrieved": retrieved})
    assert any("rag_unsaved_detail" in e for e in bad)


def test_claim_schema_accepts_rag():
    assert Claim(kind="rag", ref_id="AUTO_029_rag_mem_1",
                 value="chở chó Golden").kind == "rag"


class _CapturingFake(FakeProvider):
    def __init__(self):
        super().__init__()
        self.systems: list[str] = []

    def chat(self, messages, tools):
        for m in messages:
            if m.get("role") == "system":
                self.systems.append(m.get("content", ""))
        return super().chat(messages, tools)


def test_pipeline_rag_evidence(session):
    _index_small(session, "AUTO_029")
    assert load_index_meta(session)["provider"] == "tfidf-v1"
    pipe = Pipeline(session, _CapturingFake(), FakeSender())
    r = pipe.run(TurnRequest(
        persona_id="AUTO_029", post_text="Bài về Creta.",
        post_vehicle_models=["Hyundai Creta"],
        actor_id="actor-1", text="Trời mưa đường ngập thì sao bác?"))
    assert any("RAG_EVIDENCE" in s for s in pipe.provider.systems)
    assert r.trace.get("rag_evidence"), r.trace.get("rag_error")
    # Dedupe: chunk RAG trùng story đã lấy thì không xuất hiện lại.
    story_ids = {d.get("event_id")
                 for d in r.trace.get("retrieval_evidence", [])}
    assert all(d["source_ref"] not in story_ids
               for d in r.trace.get("rag_evidence", []))


def test_migration_004_applies_sqlite(tmp_path):
    db = sqlite3.connect(tmp_path / "m004.db")
    db.executescript((ROOT / "migrations" / "sqlite" / "004_rag.sql")
                     .read_text())
    tables = {r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"rag_chunks", "rag_index_meta"} <= tables
    db.close()
