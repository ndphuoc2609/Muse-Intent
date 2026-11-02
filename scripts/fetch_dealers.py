"""Lấy danh sách đại lý từ Google Sheet 'Phân bổ tỉ trọng data' (tab
'Danh sách đại lý + email + vị trí', gid=0) -> data/dealers.json.

CHỈ lấy dữ kiện phục vụ gợi ý đại lý: mã, tên, viết tắt, khu vực,
tỉnh thành, địa chỉ. KHÔNG lấy email nhân sự marketing, KHÔNG lấy
SĐT cá nhân gắn trong ô địa chỉ (một số ô có dạng 'Tên người - 09xx'
trước địa chỉ thật): những phần đó bị lược khi làm sạch, persona
không bao giờ được chia sẻ chúng.

Dùng: .venv/bin/python scripts/fetch_dealers.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

SPREADSHEET_ID = "1XCQMw8jSrEoDeiherh2SiCg9chbh-HTVrTYAOgZyUd0"
TAB = "Danh sách đại lý + email + vị trí"

PHONE_RE = re.compile(r"0[\d\s.\-]{8,13}\d")
ADDRESS_MARKERS = ("số", "đường", "ql", "lô", "tổ", "phường", "xã", "tp",
                   "khu", "ấp", "thôn", "km", "cụm", "kcn")


def clean_address(cell: str) -> str:
    """Bỏ SĐT + dòng chỉ còn tên người; giữ lại phần địa chỉ thật."""
    kept = []
    for line in (cell or "").split("\n"):
        line = PHONE_RE.sub("", line).strip(" -,\t")
        if not line:
            continue
        low = line.lower()
        if any(m in low for m in ADDRESS_MARKERS):
            kept.append(line)
        elif re.search(r"\d", line):
            # Dòng còn số nhưng không có marker địa chỉ (VD '1S:', '3S:')
            # vẫn là địa chỉ nhiều cơ sở — giữ.
            kept.append(line)
        # Dòng còn lại sau khi bỏ SĐT mà không có số/marker (tên người
        # đứng trước SĐT) thì bỏ.
    return "; ".join(kept)


def main() -> int:
    out = subprocess.run(
        ["/opt/hatch/bin/hatch_gws_cli", "sheets", "spreadsheets",
         "values", "get", "--params", json.dumps({
             "spreadsheetId": SPREADSHEET_ID,
             "range": f"{TAB}!A1:H200"})],
        capture_output=True, text=True, check=True)
    rows = json.loads(out.stdout).get("values", [])
    header, data = rows[0], rows[1:]
    assert header[:8] == ["Mã ĐL", "Tên Đại lý", "Viết tắt",
                          "Email nhân sự Marketing", "Địa chỉ nhận thư",
                          "Phụ trách", "Khu vực", "Tỉnh thành"], header
    dealers = []
    for r in data:
        if not r or not r[0]:
            continue
        r = r + [""] * (8 - len(r))
        dealers.append({
            "code": r[0].strip(), "name": r[1].strip(),
            "short": r[2].strip(), "region": r[6].strip(),
            "province": r[7].strip(),
            "address": clean_address(r[4])})
    root = Path(__file__).resolve().parents[1]
    path = root / "data" / "dealers.json"
    path.write_text(json.dumps(
        {"source": f"Google Sheet {SPREADSHEET_ID} tab '{TAB}'",
         "dealers": dealers}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"dealers={len(dealers)} -> {path}")
    bad = [d for d in dealers
           if PHONE_RE.search(d["address"]) or "@" in d["address"]]
    print(f"chunk chứa SĐT/email còn sót: {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
