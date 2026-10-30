"""Typed value parsing and comparison (validator §4.2).

Thay phép so khớp chung (substring / token overlap) bằng kiểm tra theo
kiểu dữ liệu cho các giá trị có cấu trúc. Mục đích: "599 triệu" và
"599.000.000 đồng" là tương đương, nhưng "5999 triệu" KHÔNG được khớp
"599 triệu" chỉ vì cùng chứa từ "triệu" (lỗi cũ: overlap token 1/2 = 0.5).

Các kiểu được hỗ trợ:
- money: số + đơn vị triệu / tỷ / nghìn / đồng (cả dạng "599 triệu đồng",
  "599.000.000", "1,2 tỷ"). Chuẩn hóa về VND (int).
- percent: "20%" -> 20.0
- distance: "500 km" -> 500.0
- date: "2024-03", "tháng 03/2024", "15/03/2024", "2024-03-15" ->
  (year, month, day|None, precision). Chỉ so sánh ở độ chính xác thấp
  hơn trong hai bên (month vs day: so tháng).

Giá trị không thuộc kiểu nào thì so bằng chuỗi chuẩn hóa chính xác,
không dùng substring: "trắng sữa" không khớp "trắng", "Creta" không
khớp "Creta 1.5" nếu fact ghi rõ phiên bản khác (trừ khi bên kia là
tiền tố model — việc đó thuộc về retrieval theo model/trim, không
phải validator).
"""
from __future__ import annotations

import re
import unicodedata

_VN_NUM = r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "")
    return re.sub(r"\s+", " ", s.lower().strip())


def _to_float(num: str) -> float:
    # "1.234.567" (dấu . phân cách nghìn) vs "1,5" (dấu , thập phân):
    # nếu có cả . và , thì . là nghìn, , là thập phân; nếu chỉ có . và
    # nhóm sau dài 3 chữ số thì là nghìn, ngược lại là thập phân.
    if "," in num and "." in num:
        return float(num.replace(".", "").replace(",", "."))
    if "," in num:
        parts = num.split(",")
        if len(parts) == 2 and len(parts[1]) == 3 and len(parts[0]) <= 3:
            # "599,000" dạng nghìn — hiếm trong tiếng Việt, vẫn xử lý.
            return float(parts[0] + parts[1])
        return float(num.replace(",", "."))
    if "." in num:
        parts = num.split(".")
        if all(len(p) == 3 for p in parts[1:]) and len(parts) > 1:
            return float("".join(parts))
        return float(num)
    return float(num)


def parse_money(text: str) -> list[int]:
    """Trả về danh sách VND (int) tìm thấy trong text."""
    out = []
    pat = re.compile(
        _VN_NUM + r"\s*(tỷ|ty|triệu|trieu|tr|nghìn|nghin|k|đồng|dong|đ|vnd)?\b",
        re.IGNORECASE)
    for m in pat.finditer(text or ""):
        num_s, unit = m.group(1), (m.group(2) or "").lower()
        try:
            v = _to_float(num_s)
        except ValueError:
            continue
        if unit in ("tỷ", "ty"):
            out.append(int(round(v * 1_000_000_000)))
        elif unit in ("triệu", "trieu", "tr"):
            out.append(int(round(v * 1_000_000)))
        elif unit in ("nghìn", "nghin", "k"):
            out.append(int(round(v * 1_000)))
        elif unit in ("đồng", "dong", "đ", "vnd"):
            # "599.000.000 đồng" đã là VND đầy đủ; "599 đồng" thì giữ 599.
            out.append(int(round(v)))
        else:
            # Số trần không đơn vị: chỉ coi là tiền khi >= 1 triệu (dạng
            # "599000000") để tránh nhầm số lượng, năm, mã xe.
            if v >= 1_000_000:
                out.append(int(round(v)))
    return out


def parse_percent(text: str) -> list[float]:
    return [float(_to_float(m.group(1)))
            for m in re.finditer(_VN_NUM + r"\s*%", text or "")]


def parse_distance_km(text: str) -> list[float]:
    return [float(_to_float(m.group(1)))
            for m in re.finditer(_VN_NUM + r"\s*km\b", text or "",
                                 re.IGNORECASE)]


def parse_dates(text: str) -> list[tuple]:
    """Mỗi date -> (year, month, day|None, precision)."""
    out = []
    for m in re.finditer(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text or ""):
        out.append((int(m.group(1)), int(m.group(2)), int(m.group(3)), "day"))
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", text or ""):
        out.append((int(m.group(3)), int(m.group(2)), int(m.group(1)), "day"))
    for m in re.finditer(r"\b(20\d{2})-(\d{1,2})\b", text or ""):
        out.append((int(m.group(1)), int(m.group(2)), None, "month"))
    for m in re.finditer(r"tháng\s*(\d{1,2})\s*(?:năm\s*)?(20\d{2})",
                         text or "", re.IGNORECASE):
        out.append((int(m.group(2)), int(m.group(1)), None, "month"))
    for m in re.finditer(r"\b(\d{1,2})/(20\d{2})\b", text or ""):
        out.append((int(m.group(2)), int(m.group(1)), None, "month"))
    # Khử trùng, giữ thứ tự.
    seen, uniq = set(), []
    for d in out:
        if d not in seen:
            seen.add(d); uniq.append(d)
    return uniq


def _dates_match(a: tuple, b: tuple) -> bool:
    if a[0] != b[0] or a[1] != b[1]:
        return False
    if a[3] == "month" or b[3] == "month":
        return True
    return a[2] == b[2]


def typed_match(claim_value: str, fact_value: str) -> bool | None:
    """So khớp theo kiểu. Trả True/False nếu cả hai bên đều có cùng kiểu
    dữ liệu có cấu trúc; None nếu không có kiểu chung để kết luận (gọi
    tiếp bước so chuỗi chính xác)."""
    for parser, matcher in (
        (parse_money, lambda a, b: a == b),
        (parse_percent, lambda a, b: abs(a - b) < 1e-9),
        (parse_distance_km, lambda a, b: abs(a - b) < 1e-9),
        (parse_dates, _dates_match),
    ):
        ca, fa = parser(claim_value), parser(fact_value)
        if ca and fa:
            return any(matcher(c, f) for c in ca for f in fa)
    return None


# Từ đệm mà model hay gắn vào claim value khi diễn đạt tự nhiên
# ("Đang đi Hyundai Creta", "Xe của mình màu trắng", "Bản 1.5 Cao cấp").
# Chỉ strip ở ĐẦU claim (và tiểu từ discourse ở CUỐI), rồi mới so chính
# xác với fact chuẩn — nên các policy chặt vẫn giữ nguyên:
# - "trắng sữa" không strip được gì ở đầu -> vẫn khác "trắng".
# - "Creta" không chứa đủ token của fact "Creta 1.5" -> vẫn khác.
# - Giá trị có kiểu (tiền/ngày/%/km) đã xử lý ở typed_match trước, không
#   đi qua đường này, nên "5999 triệu" vẫn khác "599 triệu".
_CLAIM_LEADING_WRAPPERS = {
    "đang", "đi", "mình", "của", "xe", "màu", "chiếc", "con", "bản",
    "mẫu", "dòng", "là", "đây", "này", "thì", "mình đang", "nhà mình",
}
_CLAIM_TRAILING_PARTICLES = {
    "nhé", "nha", "đó", "ạ", "thôi", "bạn", "rồi", "đấy",
}


def strip_claim_wrappers(value: str) -> str:
    """Bỏ tiền tố đệm + hậu tố discourse khỏi claim value tự nhiên."""
    tokens = _norm(value).split()
    while tokens and tokens[0] in _CLAIM_LEADING_WRAPPERS:
        tokens.pop(0)
    while tokens and tokens[-1] in _CLAIM_TRAILING_PARTICLES:
        tokens.pop()
    return " ".join(tokens)


def _free_text_matches(claim_value: str, fact_value: str) -> bool:
    """Claim tự nhiên khớp fact chuẩn khi fact bằng claim sau khi bỏ
    một tiền tố toàn từ đệm (và hậu tố discourse). Thử MỌI độ dài
    tiền tố thay vì strip tham lam: "Đây là xe đầu đời" khớp fact
    "xe đầu đời" ở tiền tố 2 từ (đây, là), chứ không strip luôn "xe"
    thành "đầu đời" rồi khớp sai. Ngược lại "trắng sữa" không có tiền
    tố đệm nào nên vẫn khác "trắng", và "Creta" vẫn khác "Creta 1.5"."""
    tokens = _norm(claim_value).split()
    while tokens and tokens[-1] in _CLAIM_TRAILING_PARTICLES:
        tokens.pop()
    target = _norm(fact_value)
    prefix_len = 0
    while prefix_len < len(tokens) and \
            tokens[prefix_len] in _CLAIM_LEADING_WRAPPERS:
        prefix_len += 1
    for k in range(prefix_len + 1):
        if " ".join(tokens[k:]) == target:
            return True
    return False


def value_matches(claim_value: str, fact_value: str) -> bool:
    """So khớp giá trị claim với fact: kiểu trước, chuỗi chính xác sau
    (cho phép claim tự nhiên có thêm từ đệm quanh giá trị chuẩn)."""
    typed = typed_match(claim_value, fact_value)
    if typed is not None:
        return typed
    if _norm(claim_value) == _norm(fact_value):
        return True
    return _free_text_matches(claim_value, fact_value)


def find_money(text: str) -> list[tuple[str, int]]:
    """Các khẳng định tiền trong text: (raw, VND). Bỏ qua số trần
    không đơn vị < 1 triệu để tránh nhầm năm/số lượng."""
    out = []
    pat = re.compile(
        _VN_NUM + r"\s*(tỷ|ty|triệu|trieu|tr|nghìn|nghin|đồng|dong|đ|vnd)\b",
        re.IGNORECASE)
    for m in pat.finditer(text or ""):
        v = parse_money(m.group(0))
        if v:
            out.append((m.group(0).strip(), v[0]))
    # Dạng "599.000.000" không đơn vị nhưng rõ là VND đầy đủ.
    for m in re.finditer(r"\b\d{1,3}(?:\.\d{3}){2,}\b", text or ""):
        v = parse_money(m.group(0))
        if v and all(v[0] != x[1] for x in out):
            out.append((m.group(0), v[0]))
    return out
