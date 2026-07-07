"""CSV 入出力ヘルパー（UTF-8 / BOM・Shift_JIS自動判別）。"""
from __future__ import annotations

import csv
from dataclasses import asdict, fields
from pathlib import Path
from typing import List, Type


def read_dicts(path: Path) -> List[dict]:
    """CSVを辞書リストで読む。UTF-8(BOM)→cp932 の順でデコードを試みる。"""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    reader = csv.DictReader(text.splitlines())
    return [{(k.strip() if k else k): (v.strip() if isinstance(v, str) else v)
             for k, v in row.items()} for row in reader]


def write_dataclasses(path: Path, records: List, cls: Type) -> None:
    """dataclass群をCSVへ（列順はdataclass定義順）。空でもヘッダを出力。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [f.name for f in fields(cls)]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in records:
            w.writerow(asdict(r))


def write_rows(path: Path, rows: List[dict], cols: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
