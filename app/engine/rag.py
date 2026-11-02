"""RAG persona-only: chunking dữ liệu persona thành vector, và truy xuất
chunk của ĐÚNG persona đang nói chuyện.

Chunking (``build_chunks``) — một persona -> nhiều chunk, mỗi chunk một
loại dữ kiện, id ổn định để build lại là idempotent:
- memory:  text = memory nguyên văn. id ``{code}_rag_mem_{i}`` — KHÁC
  story event_id ({code}_mem_{i}) để không đụng ref_id của story path
  (cùng một memory có thể xuất hiện ở cả hai kênh với kind khác nhau:
  story qua RELEVANT_MEMORIES, rag qua RAG_EVIDENCE). Id dùng gạch
  dưới, không dùng tiền tố ``rag:`` có dấu hai chấm: live test cho thấy
  model tự lược tiền tố khi claim (``rag:AUTO_029:mem:3`` thành
  ``AUTO_029:mem:3``) làm validator báo unsourced.
- perspective: text = perspective nguyên văn. id ``{code}_rag_perspective``.
- profile: text = câu ghép từ các field profile (chỉ field có giá trị),
  mỗi field gắn nhãn, KHÔNG bịa thêm. id ``{code}_rag_profile``.
- post: text = ``Chủ đề: {topic}. {hook}``. id ``{code}_rag_post_{post_id}``.

KHÔNG chunk: role, personality_traits, hashtag tags, category nguồn,
multi_angle_ready. Role/traits là nhãn archetype, không phải lời persona
nói — đưa vào prompt sẽ khiến model diễn nhãn thay vì nói chuyện tự
nhiên, trái quyết định no-role. Hashtag/category là metadata phân loại,
không phải nội dung kể được.

KHÔNG chunk memory qua story path? Ngược lại: memory VẪN có chunk RAG
riêng (trên), vì story path chỉ chấm điểm keyword/entity/tag còn RAG
chấm điểm vector (TF-IDF/neural) — hai kênh bổ sung nhau, model dùng
kênh nào thì claim kind của kênh đó.

Truy xuất (``retrieve_chunks``): lọc đúng persona_id (không bao giờ
lấy chunk của persona khác — mỗi persona chỉ được kể dữ kiện của mình),
cosine giữa query vector và chunk vector, giữ score >= threshold,
tối đa limit, xếp theo score giảm dần. Threshold do build script ghi
vào meta (mỗi provider một ngưỡng mặc định khác nhau, vì phân phối
cosine TF-IDF và neural khác nhau).
"""
from __future__ import annotations

import json

from sqlalchemy import select

from ..models import RagChunk, RagIndexMeta

# Ngưỡng đo thực tế trên corpus này (query mẫu, AUTO_029): chunk liên
# quan đạt 0.118–0.276, nhiễu ≤0.095 — ngưỡng TF-IDF 0.10 tách được hai
# nhóm với biên mỏng, nên pipeline chỉ lấy tối đa 3 chunk và validator
# vẫn là cửa cuối. Neural (st) có phân phối cosine cao hơn hẳn nên
# ngưỡng riêng 0.35.
DEFAULT_THRESHOLDS = {"tfidf-v1": 0.10, "st-v1": 0.35}
DEFAULT_LIMIT = 3


def build_chunks(record: dict) -> list[dict]:
    """Một record persona (từ data/personas.json) -> list chunk."""
    code = record["code"]
    chunks: list[dict] = []
    for i, text in enumerate(record.get("memory") or [], start=1):
        text = (text or "").strip()
        if text:
            chunks.append({
                "id": f"{code}_rag_mem_{i}",
                "persona_id": code, "chunk_type": "memory",
                "source_ref": f"{code}_mem_{i}", "text": text})
    perspective = (record.get("perspective") or "").strip()
    if perspective:
        chunks.append({
            "id": f"{code}_rag_perspective",
            "persona_id": code, "chunk_type": "perspective",
            "source_ref": code, "text": perspective})
    life = record.get("lifestyle_context") or {}
    parts: list[str] = []
    if record.get("occupation"):
        parts.append(f"Nghề nghiệp: {record['occupation']}.")
    if life.get("daily_commute"):
        parts.append(f"Nhịp sinh hoạt: {life['daily_commute']}.")
    if life.get("family_status"):
        parts.append(f"Gia đình: {life['family_status']}.")
    if life.get("hobbies"):
        parts.append("Sở thích: " + ", ".join(life["hobbies"]) + ".")
    if life.get("practical_painpoints"):
        parts.append("Điểm đau: " + "; ".join(
            life["practical_painpoints"]) + ".")
    if parts:
        chunks.append({
            "id": f"{code}_rag_profile",
            "persona_id": code, "chunk_type": "profile",
            "source_ref": code, "text": " ".join(parts)})
    for post in record.get("post_history") or []:
        topic = (post.get("topic") or "").strip()
        hook = (post.get("hook") or "").strip()
        text = " ".join(p for p in
                        (f"Chủ đề: {topic}." if topic else "", hook)
                        if p).strip()
        if text and post.get("post_id"):
            chunks.append({
                "id": f"{code}_rag_post_{post['post_id']}",
                "persona_id": code, "chunk_type": "post",
                "source_ref": post["post_id"], "text": text})
    return chunks


def cosine(a, b) -> float:
    """Cosine cho cả vector thưa (dict) và dense (list). Vector đã
    L2-chuẩn hoá lúc embed nên dot = cosine; vẫn chia norm phòng thủ
    cho vector dense chưa chuẩn hoá."""
    if isinstance(a, dict) and isinstance(b, dict):
        if len(a) > len(b):
            a, b = b, a
        return sum(w * b[t] for t, w in a.items() if t in b)
    import math
    import numpy as np
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def load_index_meta(session) -> dict:
    """Meta index thành dict phẳng; {} nếu chưa build index."""
    out = {}
    for row in session.execute(select(RagIndexMeta)).scalars().all():
        try:
            out[row.key] = json.loads(row.value)
        except (ValueError, TypeError):
            out[row.key] = row.value
    return out


def retrieve_chunks(session, persona_id: str, query_vector,
                    limit: int = DEFAULT_LIMIT,
                    threshold: float = 0.15) -> list[dict]:
    """Top chunk của đúng persona này, score >= threshold."""
    scored = []
    for row in session.execute(
            select(RagChunk).where(
                RagChunk.persona_id == persona_id)).scalars().all():
        try:
            vec = json.loads(row.embedding)
        except (ValueError, TypeError):
            continue
        score = cosine(query_vector, vec)
        if score >= threshold:
            scored.append({
                "chunk_id": row.id, "chunk_type": row.chunk_type,
                "source_ref": row.source_ref, "text": row.text,
                "score": round(score, 4)})
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:limit]
