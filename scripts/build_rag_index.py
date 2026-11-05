"""Build RAG index: persona + (tùy chọn) company.

Đọc data/personas.json -> chunk (xem rag.build_chunks) -> embed ->
ghi rag_chunks + rag_index_meta. Build lại là thay toàn bộ index
(chunk id ổn định theo nguồn nên idempotent).

--company data/dealers.json: thêm chunk đại lý (domain='company',
persona_id NULL, xem rag.build_company_chunks) vào CÙNG index để
TF-IDF fit chung vocab (tên đại lý, tỉnh thành đều có trong vocab).
Company chunk tách riêng khỏi corpus persona ở tầng retrieval
(retrieve_company_chunks lọc domain, retrieve_chunks lọc persona_id)
— không bao giờ trộn hai miền.

Dùng: .venv/bin/python scripts/build_rag_index.py [--provider tfidf|st]
       [--threshold 0.15] [--db sqlite:///./persona_dialogue.db]
       [--company data/dealers.json]

--provider tfidf (mặc định): local, không mạng, không phí.
--provider st: neural local, cần cài requirements-rag.txt trước; nếu
  thiếu thì script exit 2, KHÔNG tự fallback sang tfidf (index phải là
  lựa chọn có chủ ý, vì query và chunk phải cùng provider).

Script seed() trước để persona tồn tại trong DB (chunk có FK tới
personas.id). Sau khi build, server đang chạy phải restart mới thấy
index mới nếu nó đã load meta (pipeline đọc meta mỗi lượt nên thực ra
không cần restart — nhưng DB file phải đúng file server đang dùng).
"""
import argparse
import collections
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import init_db
from app.engine.rag import DEFAULT_THRESHOLDS, build_chunks
from app.models import RagChunk, RagIndexMeta
from app.seed import seed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["tfidf", "st"],
                    default="tfidf")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--data", default=None)
    ap.add_argument("--company", default=None)
    ap.add_argument("--db", default="sqlite:///./persona_dialogue.db")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    data_path = Path(args.data) if args.data else root / "data" / "personas.json"
    payload = json.loads(data_path.read_text(encoding="utf-8"))
    records = payload["personas"] if isinstance(payload, dict) \
        else payload
    # Version nguồn: SHA file dữ liệu lúc build. Chunk nào cũng đóng dấu
    # SHA của file nguồn của nó để pipeline so với SHA lúc seed mới nhất.
    from app.engine.rag import sha_bytes
    persona_sha = sha_bytes(data_path.read_bytes())
    company_sha = None

    engine = create_engine(args.db, connect_args={"check_same_thread": False}
                           if args.db.startswith("sqlite") else {})
    init_db(engine)
    session = sessionmaker(bind=engine)()
    seed(session)

    t0 = time.time()
    all_chunks: list[dict] = []
    for rec in records:
        all_chunks.extend(build_chunks(rec))
    company_source = None
    if args.company:
        from app.engine.rag import build_company_chunks
        company_path = Path(args.company)
        company_payload = json.loads(
            company_path.read_text(encoding="utf-8"))
        dealers = company_payload["dealers"] \
            if isinstance(company_payload, dict) else company_payload
        company_chunks = build_company_chunks(dealers)
        all_chunks.extend(company_chunks)
        company_source = str(company_path)
        company_sha = sha_bytes(company_path.read_bytes())
    texts = [c["text"] for c in all_chunks]

    if args.provider == "tfidf":
        from app.engine.embeddings import TfidfEmbedder
        emb = TfidfEmbedder().fit(texts)
        vectors = emb.embed(texts)
        meta: dict = {"provider": TfidfEmbedder.PROVIDER,
                      "state": emb.state(),
                      "dims": f"vocab={len(emb.idf)}"}
    else:
        try:
            from app.engine.embeddings import SentenceTransformerEmbedder
            emb = SentenceTransformerEmbedder()
            vectors = []
            for i in range(0, len(texts), 100):
                vectors.extend(emb.embed(texts[i:i + 100]))
        except ImportError as exc:
            print(f"Thiếu sentence-transformers, không build index st: {exc}\n"
                  "Cài: pip install -r requirements-rag.txt rồi chạy lại "
                  "với --provider st, hoặc build --provider tfidf.",
                  file=sys.stderr)
            return 2
        meta = {"provider": SentenceTransformerEmbedder.PROVIDER,
                "model": emb.model_name, "dims": emb.dims}

    threshold = args.threshold if args.threshold is not None \
        else DEFAULT_THRESHOLDS[meta["provider"]]
    meta.update({
        "threshold": threshold,
        "chunk_count": len(all_chunks),
        "company_chunk_count": sum(
            1 for c in all_chunks if c.get("domain") == "company"),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": str(data_path),
        "company_source": company_source,
        "persona_sha": persona_sha,
        "company_sha": company_sha,
    })

    session.query(RagChunk).delete()
    # Seed() đã chạy ở đầu build và ghi seed_persona_sha; wipe meta dưới
    # đây sẽ xóa nó nên phải giữ lại trước khi xóa (nếu không status sẽ
    # kẹt ở unknown ngay sau build). Với --data mặc định thì giá trị này
    # bằng persona_sha (cùng file) nên status ra ready; với --data khác
    # file seed thì hai SHA lệch nhau và status ra stale — đúng, vì đó
    # là lệch nguồn thật giữa seed và build.
    _seed_row = session.execute(
        select(RagIndexMeta).where(
            RagIndexMeta.key == "seed_persona_sha")).scalars().first()
    _kept_seed_sha = None
    if _seed_row is not None:
        try:
            _kept_seed_sha = json.loads(_seed_row.value)
        except (ValueError, TypeError):
            _kept_seed_sha = _seed_row.value
    meta["seed_persona_sha"] = _kept_seed_sha
    session.query(RagIndexMeta).delete()
    for chunk, vec in zip(all_chunks, vectors):
        session.add(RagChunk(id=chunk["id"],
                             persona_id=chunk["persona_id"],
                             domain=chunk.get("domain", "persona"),
                             chunk_type=chunk["chunk_type"],
                             source_ref=chunk["source_ref"],
                             text=chunk["text"],
                             source_sha=(company_sha
                                         if chunk.get("domain") == "company"
                                         else persona_sha),
                             embedding=json.dumps(vec, ensure_ascii=False)))
    for key, value in meta.items():
        session.add(RagIndexMeta(key=key, value=json.dumps(
            value, ensure_ascii=False)))
    session.commit()

    by_type = collections.Counter(c["chunk_type"] for c in all_chunks)
    print(f"provider={meta['provider']} dims={meta['dims']} "
          f"threshold={threshold}")
    print(f"chunks={len(all_chunks)} by_type={dict(by_type)} "
          f"personas={len(records)} "
          f"company={meta['company_chunk_count']} "
          f"time_s={time.time() - t0:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
