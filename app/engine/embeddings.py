"""Embedding provider cho RAG persona-only.

Hai provider, cùng một interface: ``embed(texts) -> list[vector]``,
vector là dict thưa (TF-IDF) hoặc list dense (neural). Retrieval
(``rag.py``) tự xử cả hai dạng, nên đổi provider không đổi code
truy xuất — chỉ đổi vector trong index (build lại index là bắt buộc,
vì query và chunk phải cùng một không gian vector).

- ``TfidfEmbedder``: local, không mạng, không tốn phí. Vector thưa
  TF-IDF trên từ + char 4-gram (đã bỏ dấu), L2-chuẩn hoá. Đủ tốt cho
  corpus persona này vì chủ đề hẹp (7 topic chính): truy vấn trùng từ
  khóa sẽ khớp mạnh; đổi lại, diễn đạt khác từ hoàn toàn (paraphrase
  thuần túy, đồng nghĩa) thì TF-IDF không bắt được — đó là giới hạn
  thật của provider này, không phải của kiến trúc RAG.
- ``SentenceTransformerEmbedder``: neural local (paraphrase-multilingual,
  hiểu tiếng Việt + diễn đạt khác từ). Model load lười (lazy) để
  server/test không trả chi phí import torch khi không dùng RAG.
  Cài đặt tùy chọn: ``requirements-rag.txt`` (không nằm trong
  requirements.txt chính vì nặng).

Không có provider OpenRouter embeddings trong v1: key OpenRouter hiện
tại bị workspace guardrail chặn endpoint embeddings (HTTP 404
'model-ignored-by-guardrail' khi probe ngày 2026-11-02). Muốn dùng
neural qua API thì mở guardrail cho embedding model rồi thêm provider
thứ ba theo đúng interface này — không sửa retrieval.
"""
from __future__ import annotations

import math
import re
import unicodedata


def _strip(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text or "")
        if unicodedata.category(c) != "Mn").lower()


def _terms(text: str) -> set[str]:
    """Từ + char 4-gram (tiền tố 'c:') trên text đã bỏ dấu.

    Char gram để truy vấn không dấu / gõ sai một ký tự vẫn khớp chunk
    có dấu ('ngap' vẫn gặp 'ngập' qua gram 'ngap'/'ngập' -> 'ngap'
    sau khi bỏ dấu).
    """
    words = re.findall(r"\w+", _strip(text))
    out = set(words)
    for w in words:
        if len(w) >= 4:
            for i in range(len(w) - 3):
                out.add("c:" + w[i:i + 4])
    return out


class TfidfEmbedder:
    """TF-IDF nhị phân (có mặt = 1) nhân IDF, L2-chuẩn hoá.

    State bền vững: ``{"idf": {...}, "n_docs": N}`` — lưu trong
    rag_index_meta để lúc query không cần fit lại. Từ lạ (không có
    trong idf) bị bỏ qua, đúng chuẩn TF-IDF.
    """

    PROVIDER = "tfidf-v1"

    def __init__(self, idf: dict[str, float] | None = None,
                 n_docs: int = 0):
        self.idf = idf or {}
        self.n_docs = n_docs

    def fit(self, texts: list[str]) -> "TfidfEmbedder":
        df: dict[str, int] = {}
        for t in texts:
            for term in _terms(t):
                df[term] = df.get(term, 0) + 1
        n = len(texts)
        self.idf = {term: math.log((n + 1) / (c + 1)) + 1.0
                    for term, c in df.items()}
        self.n_docs = n
        return self

    def state(self) -> dict:
        return {"provider": self.PROVIDER, "idf": self.idf,
                "n_docs": self.n_docs}

    @classmethod
    def from_state(cls, state: dict) -> "TfidfEmbedder":
        return cls(idf=dict(state.get("idf") or {}),
                   n_docs=int(state.get("n_docs") or 0))

    def _one(self, text: str) -> dict[str, float]:
        vec = {t: self.idf[t] for t in _terms(text) if t in self.idf}
        norm = math.sqrt(sum(v * v for v in vec.values()))
        if norm > 0:
            vec = {t: v / norm for t, v in vec.items()}
        return vec

    def embed(self, texts: list[str]) -> list[dict[str, float]]:
        return [self._one(t) for t in texts]


class SentenceTransformerEmbedder:
    """Neural local, lazy-load. Vector dense đã L2-chuẩn hoá."""

    PROVIDER = "st-v1"

    def __init__(self, model_name: str =
                 "paraphrase-multilingual-MiniLM-L12-v2"):
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    @property
    def dims(self) -> int:
        return int(self._load().get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = self._load().encode(
            texts, normalize_embeddings=True, show_progress_bar=False)
        return [list(map(float, v)) for v in vecs]


def build_embedder_from_meta(meta: dict):
    """Dựng embedder lúc query từ meta đã lưu lúc build index.

    - tfidf: nạp idf từ meta (rẻ, làm mỗi lượt được).
    - st: trả về embedder dùng model singleton (load một lần, các lượt
      sau dùng lại; lượt đầu tiên trả phí load ~vài giây, chỉ trong
      sandbox).
    Trả về None nếu meta không nhận diện được provider.
    """
    provider = (meta or {}).get("provider")
    if provider == TfidfEmbedder.PROVIDER:
        return TfidfEmbedder.from_state((meta.get("state") or {}))
    if provider == SentenceTransformerEmbedder.PROVIDER:
        return _ST_SINGLETON.setdefault(
            (meta.get("model") or "paraphrase-multilingual-MiniLM-L12-v2"),
            SentenceTransformerEmbedder(
                meta.get("model")
                or "paraphrase-multilingual-MiniLM-L12-v2"))
    return None


_ST_SINGLETON: dict[str, SentenceTransformerEmbedder] = {}
