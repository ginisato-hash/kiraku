"""キャッシュ済み Beds24 raw JSON の読み込み（API再呼び出しは絶対に行わない）。

data/raw/beds24/<month>/<month>.json を読む点は既存の
src/yuge_finance/reports/beds24_audit.py の raw_path() と同じ規約に合わせる
(Beds24Client.fetch_month が書き込むパスそのもの)。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import List

from .. import config


def raw_json_path(month: str, data_root: Path = None) -> Path:
    """対象月(YYYY-MM)の raw JSON パス。data_root省略時は config.DATA_DIR。"""
    root = data_root or config.DATA_DIR
    return root / "raw" / "beds24" / month / f"{month}.json"


def load_raw_bookings_for_months(months: List[str], data_root: Path = None) -> List[dict]:
    """各月の raw JSON を読み、予約dictを1つのリストへ連結する。

    対象月のファイルがまだ存在しない場合は静かにスキップする(未来月・未取得月は
    存在しないのが正常なケースのため、エラーにしない)。JSONが壊れている場合も
    同様にスキップする(空リストとして扱う)。
    """
    out: List[dict] = []
    for month in months:
        p = raw_json_path(month, data_root=data_root)
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            out.extend(data)
    return out


def months_covering_date_range(start_date: str, end_date: str, safety_days: int = 3) -> List[str]:
    """[start_date - safety_days, end_date + safety_days] の各日付が属する月(YYYY-MM)の
    重複無し一覧を、日付順に返す。

    チェックイン/チェックアウトが対象日の月境界をまたぐ可能性があるため、
    前後に安全マージンを持たせて余分に月を読み込む(読みすぎても副作用はない。
    load_raw_bookings_for_monthsは該当月のファイルが無ければ単にスキップする)。
    """
    start = date.fromisoformat(start_date) - timedelta(days=safety_days)
    end = date.fromisoformat(end_date) + timedelta(days=safety_days)
    if end < start:
        start, end = end, start
    months: List[str] = []
    seen = set()
    d = start
    while d <= end:
        key = f"{d.year:04d}-{d.month:02d}"
        if key not in seen:
            seen.add(key)
            months.append(key)
        d += timedelta(days=1)
    return months
