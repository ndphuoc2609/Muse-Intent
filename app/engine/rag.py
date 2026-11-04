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
import unicodedata

from sqlalchemy import select

from ..models import CustomerFact, RagChunk, RagIndexMeta


def _strip(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text or "")
        if unicodedata.category(c) != "Mn").lower()


# Từ khóa mở cổng truy xuất company (đại lý): chỉ khi khách thể hiện
# quan tâm mua xe / hỏi đại lý / liên hệ thì mới đưa COMPANY_EVIDENCE
# vào prompt — lượt thường không cần, tránh model lôi đại lý vào
# chuyện không liên quan.
# Cạm bẫy: bỏ dấu làm "mưa" thành "mua" nên KHÔNG được khớp "mua" trên
# text đã bỏ dấu ("trời mưa" sẽ thành tín hiệu lead giả). "mua" chỉ
# khớp trên text gốc có dấu; các từ khóa còn lại khớp trên text bỏ dấu
# để chịu được chat không dấu. "mua" không dấu (user gõ "mua" cho cả
# mưa lẫn mua) vẫn mơ hồ — chấp nhận được vì hậu quả chỉ là đính kèm
# evidence, model vẫn chỉ dùng khi liên quan.
# "nghien cuu" = đang nghiên cứu một mẫu xe cụ thể: tín hiệu lead ở
# giai đoạn tìm hiểu (theo kịch bản Phước 2026-11-02: khai thác 1-2 câu
# rồi giới thiệu sale + xin SĐT). "tim hieu"/"tham khao" KHÔNG thêm —
# quá chung, dễ bật lead cho khách chỉ xem chơi.
LEAD_KEYWORDS = ("dai ly", "sale", "lay xe", "quan tam",
                 "bao gia", "tu van", "lien he", "khu vuc", "showroom",
                 "gan", "o dau", "dat coc", "dang tinh", "dang tim",
                 "nghien cuu")


def has_lead_signal(text: str) -> bool:
    import re
    orig = (text or "").lower()
    if re.search(r"\bmua\b", orig):
        return True
    stripped = _strip(text)
    words = set(re.findall(r"\w+", stripped))
    if words & {"sale", "showroom", "gan"}:
        return True
    return any(k in stripped for k in LEAD_KEYWORDS)


def build_company_chunks(dealers: list[dict]) -> list[dict]:
    """Một đại lý -> một chunk company. Text chỉ gồm tên/mã/khu vực/
    tỉnh/địa chỉ đã làm sạch (fetch_dealers.py đã lược email + SĐT
    cá nhân) — KHÔNG bao giờ có thông tin liên hệ cá nhân trong chunk.
    Id ổn định theo mã đại lý để build lại là idempotent."""
    chunks = []
    for d in dealers:
        code = (d.get("code") or "").strip()
        if not code:
            continue
        parts = [f"Đại lý: {d.get('name', '').strip()} ({code})."]
        if d.get("region"):
            parts.append(f"Khu vực: {d['region'].strip()}.")
        if d.get("province"):
            parts.append(f"Tỉnh thành: {d['province'].strip()}.")
        if d.get("address"):
            parts.append(f"Địa chỉ: {d['address'].strip()}.")
        chunks.append({
            "id": f"company_{code}", "persona_id": None,
            "chunk_type": "dealer", "source_ref": code,
            "domain": "company", "text": " ".join(parts)})
    return chunks


def retrieve_company_chunks(session, query_vector,
                            limit: int = 3,
                            threshold: float = 0.15) -> list[dict]:
    """Top chunk company (đại lý), không gắn persona nào — mọi persona
    đều được gợi ý cùng danh sách đại lý. Tách riêng khỏi chunk
    persona: không bao giờ trộn vào retrieve_chunks của persona."""
    scored = []
    for row in session.execute(
            select(RagChunk).where(
                RagChunk.domain == "company")).scalars().all():
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


def find_thread_area(session, thread_id: str, actor_id: str) -> str:
    """Khu vực khách đã nêu trong thread này (để bổ sung đại lý theo
    tỉnh). Khớp theo relation='location' HOẶC predicate='location':
    model có lúc lưu khu vực ở predicate mà relation=None (live
    2026-11-04: fact Hà Nội predicate='location', relation=None) nên
    chỉ khớp relation sẽ bỏ sót. Lấy fact mới nhất."""
    from sqlalchemy import or_
    row = session.execute(
        select(CustomerFact).where(
            CustomerFact.thread_id == thread_id,
            CustomerFact.actor_id == actor_id,
            or_(CustomerFact.relation == "location",
                CustomerFact.predicate == "location"),
            CustomerFact.status == "current")
        .order_by(CustomerFact.id.desc())).scalars().first()
    if row is None:
        return ""
    return row.value_normalized or row.value_raw or ""


def match_company_by_province(session, area: str,
                              limit: int = 3) -> list[dict]:
    """Khớp đại lý theo tỉnh thành CHÍNH XÁC (không qua vector): tỉnh
    trong chunk khớp area khi một bên chứa bên kia sau khi bỏ dấu
    ('Đà Nẵng' khớp 'TP Đà Nẵng'). Dùng khi đã biết khu vực của khách
    — vector TF-IDF cho câu ngắn kiểu 'Em ở Hà Nội ạ.' chỉ đạt ~0.12,
    sát ngưỡng 0.10 nên mong manh; khớp tỉnh thì chắc chắn."""
    import re
    needle = _strip(area)
    if not needle:
        return []
    out = []
    for row in session.execute(
            select(RagChunk).where(
                RagChunk.domain == "company")).scalars().all():
        m = re.search(r"Tỉnh thành: ([^.]+)\.", row.text or "")
        prov = _strip(m.group(1)) if m else ""
        if prov and (needle in prov or prov in needle):
            out.append({
                "chunk_id": row.id, "chunk_type": row.chunk_type,
                "source_ref": row.source_ref, "text": row.text,
                "score": 1.0, "match": "province"})
    out.sort(key=lambda d: d["chunk_id"])
    return out[:limit]

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
