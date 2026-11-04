"""Gate truoc LLM: luat xac dinh + cache FAQ. Mien phi, khong goi model.

Thu tu quyet dinh (an toan truoc, tiet kiem sau):
1. Dang co cau hoi cho cua actor -> "pipeline" (binh luan co the la dap an).
2. Co tin hieu lead (luot nay hoac luot truoc, pipeline da tinh) -> "pipeline".
3. Khong co noi dung (rong / chi emoji / chi dau cau) -> "bypass".
4. Khop danh sach phan hoi xac dinh (ok, da, vang, cuoi...) -> "bypass".
5. Khop ngan hang FAQ (cau tra loi da duyet) -> "faq".
6. Con lai -> "pipeline".

Gate la ham thuan: nhan text + 2 co (has_pending, has_lead) + bank,
khong doc DB, de pipeline truyen dung gia tri no da tinh va de test
khong can DB. Bank doc tu data/faq.json (cache lan dau); neu file
thieu hoac sai dinh dang -> bank rong, chi con luat (khong crash server).

Chuan hoa: NFD + bo dau + lower + map rieng d/D -> d. Ly do map rieng:
ky tu "d" (U+0111) KHONG phan ra duoi NFD nen _strip cua rag/embeddings
giu nguyen no; gate map them de mot pattern khop ca "di" va "di"
(ca hai cach go deu gap trong binh luan that).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# Binh luan xac nhan/cuoi ngan, khong co cau hoi cho -> khong can tra loi.
# Danh sach co tinh hep: chi nhung dang chac chan la phan hoi rong
# (khong mang thong tin, khong phai cau hoi). Loi cam on ("cam on")
# KHONG nam day vi no co cau tra loi mau trong FAQ ("Khong co gi nhe!").
NO_REPLY_EXACT = {
    "ok", "oke", "okay", "ok a", "ok nhe", "ok nha",
    "u", "um", "uhm", "da", "da a", "vang", "vang a",
    "haha", "kk", "kkk", "hihi",
}


@dataclass
class GateDecision:
    action: str  # "pipeline" | "bypass" | "faq"
    reason: str
    faq_id: str | None = None
    answer: str | None = None


def normalize(text: str) -> str:
    """Bo dau, lower, map d->d, chi giu chu so/chu cai/khoang trang."""
    s = "".join(
        c for c in unicodedata.normalize("NFD", text or "")
        if unicodedata.category(c) != "Mn").lower()
    s = s.replace("đ", "d").replace("Đ", "d")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _load_bank_raw(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return []
    return data["entries"]


def _compile_bank(entries: list[dict]) -> list[dict]:
    """Chuan hoa pattern luc load de match chay nhanh va nhat quan."""
    out = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("id") or not e.get("answer"):
            continue
        mode = e.get("match", "contains")
        raw = e.get("patterns") or []
        if mode == "exact":
            pats = [normalize(p) for p in raw if isinstance(p, str)]
            pats = [p for p in pats if p]
        elif mode == "contains":
            pats = []
            for p in raw:
                if isinstance(p, str):
                    toks = normalize(p).split()
                elif isinstance(p, list):
                    toks = [normalize(t) for t in p if isinstance(t, str)]
                    toks = [t for t in toks if t]
                else:
                    continue
                if toks:
                    pats.append(toks)
        else:
            continue
        if pats:
            out.append({"id": e["id"], "match": mode,
                        "patterns": pats, "answer": e["answer"]})
    return out


_BANK_CACHE: list[dict] | None = None


def load_bank(path: Path | None = None) -> list[dict]:
    """Bank FAQ da compile; cache lan dau, file thieu/loi -> []."""
    global _BANK_CACHE
    if path is not None:
        return _compile_bank(_load_bank_raw(path))
    if _BANK_CACHE is None:
        default = Path(__file__).resolve().parents[2] / "data" / "faq.json"
        _BANK_CACHE = _compile_bank(_load_bank_raw(default))
    return _BANK_CACHE


def match_faq(text: str, bank: list[dict]) -> dict | None:
    """Tra entry FAQ khop, hoac None. Exact: toan bo text khop pattern.
    Contains: moi token cua pattern deu co trong text (thu tu tu do)."""
    norm = normalize(text)
    if not norm:
        return None
    tokens = set(norm.split())
    for e in bank:
        if e["match"] == "exact":
            if norm in e["patterns"]:
                return e
        else:
            for pat in e["patterns"]:
                if all(t in tokens for t in pat):
                    return e
    return None


def decide_gate(text: str, has_pending: bool, has_lead: bool,
                bank: list[dict] | None = None) -> GateDecision:
    """Quy trinh 6 buoc o docstring module. Thu tu la hop dong:
    doi override (pending, lead) luon thang truoc moi luat tiet kiem."""
    if has_pending:
        return GateDecision("pipeline",
                            "co cau hoi cho cua actor — binh luan co the la dap an")
    if has_lead:
        return GateDecision("pipeline",
                            "co tin hieu lead — can chay du pipeline")
    norm = normalize(text)
    if not norm:
        return GateDecision("bypass", "binh luan rong / chi emoji / chi dau cau")
    if norm in NO_REPLY_EXACT:
        return GateDecision("bypass", f"phan hoi xac dinh ({norm!r})")
    entry = match_faq(text, bank if bank is not None else load_bank())
    if entry is not None:
        return GateDecision("faq",
                            f"khop FAQ entry {entry['id']}",
                            faq_id=entry["id"], answer=entry["answer"])
    return GateDecision("pipeline", "khong khop luat nao — chay du pipeline")
