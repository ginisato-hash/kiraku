"""スタッフ Daily Ops / 清掃指示 スナップショット生成（会計・売上とは完全に独立）。

data/raw/beds24/<month>/<month>.json のキャッシュ済みraw JSONのみを読み、
StaffBookingRecord/CleaningRoomStateへ変換したうえでJSONへ書き出す。
売上・価格関連フィールドは schema.py の allow-list dataclass に存在しないため
構造的に混入し得ないが、assert_no_financial_keys() で最終防御として二重チェックする。
"""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .. import config
from .cleaning_classifier import classify_cleaning_for_date
from .extract import extract_staff_booking, load_room_type_config, load_room_unit_mapping
from .raw_reader import load_raw_bookings_for_months, months_covering_date_range
from .schema import StaffBookingRecord, assert_no_financial_keys, assert_no_forbidden_cleaning_keys

# JSTタイムゾーンはこのリポジトリの既存規約(共有ユーティリティを作らず各モジュールで
# 独立定義する。accounting/beds24_revenue_logic.py の JST定義/jst_today()パターンに合わせる)
# に従い、ops/パッケージ内でローカルに定義する。import時に固定せず、呼び出しの都度
# datetime.now(timezone.utc)から計算する。
JST = timezone(timedelta(hours=9))

PROPERTY_NAME = "喜らく"
CANCELLED_LIKE_STATUSES = ("cancelled", "canceled", "black")

DEFAULT_OUT_RELATIVE_PATH = Path("data") / "output" / "latest" / "ops" / "staff_ops_snapshot.json"


def jst_today() -> date:
    return datetime.now(timezone.utc).astimezone(JST).date()


def jst_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone(JST).isoformat(timespec="seconds")


def default_target_dates(today: Optional[date] = None) -> List[str]:
    """既定の4日ウィンドウ: 前日・当日・翌日・翌々日(JST基準)。"""
    t = today or jst_today()
    return [(t + timedelta(days=offset)).isoformat() for offset in (-1, 0, 1, 2)]


def default_out_path() -> Path:
    return config.DATA_DIR / "output" / "latest" / "ops" / "staff_ops_snapshot.json"


def _is_cancelled_like(status: str) -> bool:
    return str(status or "").strip().lower() in CANCELLED_LIKE_STATUSES


def _asdict(obj) -> Dict:
    return dataclasses.asdict(obj)


def _sorted_by_booking_id(records: List[StaffBookingRecord]) -> List[StaffBookingRecord]:
    """raw JSONの並び順(ファイル書込時点のAPIページ順)に依存しないよう、booking_idで
    安定ソートする(accounting/beds24_revenue_logic.pyのdetails.sort(...)と同じ考え方)。"""
    return sorted(records, key=lambda b: b.booking_id)


def load_all_staff_bookings(target_dates: List[str], data_root: Path = None,
                            room_types_config: Dict = None,
                            room_unit_mapping: Dict = None) -> List[StaffBookingRecord]:
    """対象日の集合をカバーするのに必要な月のraw JSONを読み込み、
    StaffBookingRecordへ変換した一覧を返す(重複booking_idは除去)。"""
    if not target_dates:
        return []
    start = min(target_dates)
    end = max(target_dates)
    months = months_covering_date_range(start, end)
    raw_bookings = load_raw_bookings_for_months(months, data_root=data_root)
    room_types_config = room_types_config if room_types_config is not None else load_room_type_config()
    room_unit_mapping = room_unit_mapping if room_unit_mapping is not None else load_room_unit_mapping()

    seen_ids = set()
    records: List[StaffBookingRecord] = []
    for raw in raw_bookings:
        rec = extract_staff_booking(raw, room_types_config, room_unit_mapping)
        if rec.booking_id in seen_ids:
            continue
        seen_ids.add(rec.booking_id)
        records.append(rec)
    return records


def _build_date_bucket(records: List[StaffBookingRecord], target_date: str) -> Dict:
    arrivals = [b for b in records
               if b.checkin_date == target_date and not _is_cancelled_like(b.status)]
    departures = [b for b in records
                 if b.checkout_date == target_date and not _is_cancelled_like(b.status)]
    stayovers = [b for b in records
                if b.checkin_date and b.checkout_date
                and b.checkin_date < target_date < b.checkout_date
                and not _is_cancelled_like(b.status)]
    cleaning_rows = classify_cleaning_for_date(records, target_date)
    cleaning_dict = {"rooms": [_asdict(r) for r in cleaning_rows]}
    # cleaning出力専用の禁止キー(財務系+住所/電話/メール/パスポート/国籍等)の
    # 最終防御チェック。CleaningGuestInfoにこれらのフィールドは存在しないため
    # 構造的に混入し得ないが、将来の変更に対する回帰防止として実行する。
    assert_no_forbidden_cleaning_keys(cleaning_dict)

    return {
        "arrivals": [_asdict(b) for b in _sorted_by_booking_id(arrivals)],
        "departures": [_asdict(b) for b in _sorted_by_booking_id(departures)],
        "stayovers": [_asdict(b) for b in _sorted_by_booking_id(stayovers)],
        "cleaning": cleaning_dict,
    }


def build_staff_ops_snapshot(target_dates: List[str], data_root: Path = None) -> Dict:
    """対象日一覧について arrivals/departures/stayovers/cleaning を組み立てる。

    data_root はテスト用にraw JSONの探索ルート(config.DATA_DIRの代わり)を差し替える
    ためのオプション引数(本番実行では常にNone=config.DATA_DIRを使う)。
    """
    room_types_config = load_room_type_config()
    room_unit_mapping = load_room_unit_mapping()
    records = load_all_staff_bookings(target_dates, data_root=data_root,
                                      room_types_config=room_types_config,
                                      room_unit_mapping=room_unit_mapping)

    dates_out = {
        d: _build_date_bucket(records, d)
        for d in target_dates
    }

    snapshot = {
        "generated_at_jst": jst_now_iso(),
        "property_name": PROPERTY_NAME,
        "dates": dates_out,
    }
    # 構造的にはallow-list dataclassのため財務キーは混入し得ないが、将来フィールドが
    # 不用意に追加された場合の回帰防止として、書き出し直前に最終防御チェックを行う。
    assert_no_financial_keys(snapshot)
    return snapshot


def write_staff_ops_snapshot(snapshot: Dict, out_path: Path) -> None:
    """publish.py と同じ atomic-write パターン(一時ファイル -> os.replace相当)で書き出す。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.parent / (out_path.name + ".tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out_path)
