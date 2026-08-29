"""ops.raw_reader の月カバレッジ計算・raw JSON読み込みテスト（実データ/API呼び出し無し）。"""
import json

from yuge_finance.ops.raw_reader import (load_raw_bookings_for_months,
                                         months_covering_date_range)


def test_months_covering_single_day_within_month():
    months = months_covering_date_range("2026-09-15", "2026-09-15", safety_days=3)
    assert months == ["2026-09"]


def test_months_covering_range_crossing_month_boundary_with_safety_window():
    """2026-09-01〜2026-09-03に±3日の安全マージンを取ると、2026-08-29〜2026-09-06に
    なるため、月境界をまたいだ予約(月末チェックアウト/月初チェックイン)も
    カバーできるよう2026-08と2026-09の両方が返る。"""
    months = months_covering_date_range("2026-09-01", "2026-09-03", safety_days=3)
    assert months == ["2026-08", "2026-09"]


def test_months_covering_range_spanning_three_months():
    months = months_covering_date_range("2026-07-30", "2026-09-02", safety_days=3)
    assert months == ["2026-07", "2026-08", "2026-09"]


def test_months_covering_handles_reversed_start_end():
    months = months_covering_date_range("2026-09-03", "2026-09-01", safety_days=3)
    assert months == ["2026-08", "2026-09"]


# ---------------- raw JSON読み込み ----------------
def test_load_raw_bookings_for_months_reads_cached_json(tmp_path):
    month_dir = tmp_path / "raw" / "beds24" / "2026-09"
    month_dir.mkdir(parents=True)
    (month_dir / "2026-09.json").write_text(
        json.dumps([{"id": "1"}, {"id": "2"}], ensure_ascii=False), encoding="utf-8")

    out = load_raw_bookings_for_months(["2026-09"], data_root=tmp_path)
    assert len(out) == 2
    assert {b["id"] for b in out} == {"1", "2"}


def test_load_raw_bookings_for_months_skips_missing_month_silently(tmp_path):
    out = load_raw_bookings_for_months(["2026-12"], data_root=tmp_path)
    assert out == []


def test_load_raw_bookings_for_months_concatenates_multiple_months(tmp_path):
    for month, ids in (("2026-08", ["a"]), ("2026-09", ["b", "c"])):
        d = tmp_path / "raw" / "beds24" / month
        d.mkdir(parents=True)
        (d / f"{month}.json").write_text(
            json.dumps([{"id": i} for i in ids], ensure_ascii=False), encoding="utf-8")
    out = load_raw_bookings_for_months(["2026-08", "2026-09"], data_root=tmp_path)
    assert {b["id"] for b in out} == {"a", "b", "c"}


def test_load_raw_bookings_for_months_skips_malformed_json(tmp_path):
    d = tmp_path / "raw" / "beds24" / "2026-09"
    d.mkdir(parents=True)
    (d / "2026-09.json").write_text("{not valid json", encoding="utf-8")
    out = load_raw_bookings_for_months(["2026-09"], data_root=tmp_path)
    assert out == []
