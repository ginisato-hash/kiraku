"""部屋タイプ別KPI(ADR/日別稼働率/売上構成)の計算テスト。"""
import json

from yuge_finance import db, monthly
from yuge_finance.accounting import room_type_metrics as rtm
from yuge_finance.api.beds24_client import normalize_booking
from yuge_finance.normalize.schema import BookingRecord
from yuge_finance.reports import bi_export

EXCLUDE = ["cancelled", "canceled", "black"]

TEST_CONFIG = {
    "single": {"label": "シングル", "capacity_rooms": 2, "match": {"room_ids": ["100"]}},
    "twin": {"label": "ツイン", "capacity_rooms": 3, "match": {"room_ids": ["200"]}},
    "unknown": {"label": "未分類", "capacity_rooms": 0, "match": {"room_ids": []}},
}


def _booking(bid, checkin, checkout, room_id="100", gross=10000, status="confirmed", rooms=1):
    return BookingRecord(
        booking_id=bid, checkin_date=checkin, checkout_date=checkout,
        room_id=room_id, rooms=rooms, gross_revenue=gross, status=status,
    ).finalize()


# ---------------- ADR ----------------
def test_adr_equals_revenue_over_sold_room_nights():
    bookings = [_booking("1", "2026-08-10", "2026-08-12", gross=20000)]  # 2泊20,000円
    result = rtm.calculate_room_type_metrics(bookings, "2026-08", TEST_CONFIG, EXCLUDE)
    assert result["sold_room_nights"] == 2
    assert result["adr_gross"] == 10000


def test_adr_is_zero_when_no_sold_room_nights():
    result = rtm.calculate_room_type_metrics([], "2026-08", TEST_CONFIG, EXCLUDE)
    assert result["sold_room_nights"] == 0
    assert result["adr_gross"] == 0


# ---------------- 月跨ぎ按分 ----------------
def test_sold_room_nights_prorated_across_months():
    """2026-08-30〜09-02(3泊: 8月2泊/9月1泊)、90,000円。"""
    bookings = [_booking("1", "2026-08-30", "2026-09-02", gross=90000)]
    aug = rtm.calculate_room_type_metrics(bookings, "2026-08", TEST_CONFIG, EXCLUDE)
    sep = rtm.calculate_room_type_metrics(bookings, "2026-09", TEST_CONFIG, EXCLUDE)
    assert aug["sold_room_nights"] == 2
    assert sep["sold_room_nights"] == 1
    # revenue = price * target_month_nights / total_nights
    assert aug["room_type_revenue_mix"][0]["revenue"] == 60000
    assert sep["room_type_revenue_mix"][0]["revenue"] == 30000


# ---------------- キャンセル除外 ----------------
def test_cancelled_booking_excluded_from_revenue_and_occupancy():
    bookings = [
        _booking("1", "2026-08-10", "2026-08-11", gross=10000, status="confirmed"),
        _booking("2", "2026-08-10", "2026-08-11", gross=99999, status="cancelled"),
    ]
    result = rtm.calculate_room_type_metrics(bookings, "2026-08", TEST_CONFIG, EXCLUDE)
    assert result["sold_room_nights"] == 1
    assert sum(r["revenue"] for r in result["room_type_revenue_mix"]) == 10000
    day = next(d for d in result["room_type_daily_occupancy"]
              if d["date"] == "2026-08-10" and d["room_type"] == "single")
    assert day["sold_rooms"] == 1  # cancelled分は含まない


# ---------------- price=0 fallback反映 ----------------
def test_price_zero_fallback_price_is_reflected_in_adr():
    """normalize_booking()のprice=0 charge-lineフォールバック済みpriceがADR/revenueに使われる。"""
    raw = {
        "id": "89381508", "apiSource": "Direct", "status": "confirmed", "price": 0,
        "roomId": "100", "arrival": "2026-08-06", "departure": "2026-08-08",
        "invoiceItems": [{"type": "charge", "description": "", "lineTotal": 11800}],
    }
    rec = normalize_booking(raw)
    result = rtm.calculate_room_type_metrics([rec], "2026-08", TEST_CONFIG, EXCLUDE)
    assert result["sold_room_nights"] == 2
    assert result["room_type_revenue_mix"][0]["revenue"] == 11800
    assert result["adr_gross"] == 5900


# ---------------- 部屋タイプ分類 ----------------
def test_classify_room_type_by_room_id():
    b = _booking("1", "2026-08-10", "2026-08-11", room_id="200")
    assert rtm.classify_room_type(b, TEST_CONFIG) == "twin"


def test_unclassified_room_becomes_unknown_and_warns():
    bookings = [_booking("1", "2026-08-10", "2026-08-11", room_id="999", gross=5000)]
    result = rtm.calculate_room_type_metrics(bookings, "2026-08", TEST_CONFIG, EXCLUDE)
    mix_keys = {r["room_type"] for r in result["room_type_revenue_mix"]}
    assert "unknown" in mix_keys
    unknown_row = next(r for r in result["room_type_revenue_mix"] if r["room_type"] == "unknown")
    assert unknown_row["revenue"] == 5000  # revenueには含める
    assert any("未分類" in w for w in result["room_type_metrics_warnings"])


# ---------------- 日別稼働率 ----------------
def test_room_type_daily_occupancy_by_date_and_type():
    bookings = [
        _booking("1", "2026-08-10", "2026-08-12", room_id="100"),
        _booking("2", "2026-08-11", "2026-08-13", room_id="200"),
    ]
    result = rtm.calculate_room_type_metrics(bookings, "2026-08", TEST_CONFIG, EXCLUDE)
    d10_single = next(d for d in result["room_type_daily_occupancy"]
                      if d["date"] == "2026-08-10" and d["room_type"] == "single")
    d11_twin = next(d for d in result["room_type_daily_occupancy"]
                    if d["date"] == "2026-08-11" and d["room_type"] == "twin")
    assert d10_single["sold_rooms"] == 1
    assert d10_single["available_rooms"] == 2
    assert d10_single["occupancy_rate"] == 50.0
    assert d11_twin["sold_rooms"] == 1
    assert d11_twin["available_rooms"] == 3


def test_checkout_day_is_not_counted_as_occupied():
    bookings = [_booking("1", "2026-08-10", "2026-08-12", room_id="100")]
    result = rtm.calculate_room_type_metrics(bookings, "2026-08", TEST_CONFIG, EXCLUDE)
    d12 = next(d for d in result["room_type_daily_occupancy"]
              if d["date"] == "2026-08-12" and d["room_type"] == "single")
    assert d12["sold_rooms"] == 0  # checkout日(08-12)は稼働に含まない
    d10 = next(d for d in result["room_type_daily_occupancy"]
              if d["date"] == "2026-08-10" and d["room_type"] == "single")
    assert d10["sold_rooms"] == 1  # checkin日(08-10)は含む


def test_room_type_occupancy_chart_series_is_wide_format():
    bookings = [_booking("1", "2026-08-10", "2026-08-11", room_id="100")]
    result = rtm.calculate_room_type_metrics(bookings, "2026-08", TEST_CONFIG, EXCLUDE)
    assert len(result["room_type_occupancy_chart_series"]) == 31  # 8月は31日
    row = next(r for r in result["room_type_occupancy_chart_series"] if r["date"] == "2026-08-10")
    assert row["シングル"] == 50.0
    assert row["ツイン"] == 0.0


# ---------------- 月合計 ----------------
def test_available_room_nights_equals_capacity_sum_times_days():
    result = rtm.calculate_room_type_metrics([], "2026-08", TEST_CONFIG, EXCLUDE)
    # (single:2 + twin:3 + unknown:0) * 31日
    assert result["available_room_nights"] == 5 * 31


def test_occupancy_rate_month_equals_sold_over_available_times_100():
    bookings = [_booking("1", "2026-08-10", "2026-08-12", room_id="100")]  # 2泊
    result = rtm.calculate_room_type_metrics(bookings, "2026-08", TEST_CONFIG, EXCLUDE)
    expected = round(2 / (5 * 31) * 100, 1)
    assert result["occupancy_rate_month"] == expected


def test_revenue_mix_share_sums_to_about_100():
    bookings = [
        _booking("1", "2026-08-10", "2026-08-11", room_id="100", gross=10000),
        _booking("2", "2026-08-12", "2026-08-13", room_id="200", gross=30000),
    ]
    result = rtm.calculate_room_type_metrics(bookings, "2026-08", TEST_CONFIG, EXCLUDE)
    total_share = sum(r["share"] for r in result["room_type_revenue_mix"])
    assert abs(total_share - 100.0) < 0.5


def test_warning_when_daily_occupancy_exceeds_100_percent():
    """capacity=2のsingleに同日3室分の予約(room_quantity合計3)が入り稼働率100%超過。"""
    bookings = [
        _booking("1", "2026-08-10", "2026-08-11", room_id="100", rooms=3),
    ]
    result = rtm.calculate_room_type_metrics(bookings, "2026-08", TEST_CONFIG, EXCLUDE)
    d10 = next(d for d in result["room_type_daily_occupancy"]
              if d["date"] == "2026-08-10" and d["room_type"] == "single")
    assert d10["sold_rooms"] == 3
    assert d10["occupancy_rate"] == 150.0
    assert any("100%を超えています" in w for w in result["room_type_metrics_warnings"])


# ---------------- snapshot統合 ----------------
def test_room_type_metrics_fields_present_in_bi_snapshot(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        BookingRecord(booking_id="1", checkin_date="2026-08-10", checkout_date="2026-08-11",
                     room_id="685761", gross_revenue=10000, status="confirmed").finalize(),
    ])
    ctx = monthly.assemble("2026-08", conn)
    sev = {"all_ok": True, "critical": [], "warnings": []}
    bi_export.write_all("2026-08", ctx, checks=[], wb_checks=[], severity=sev, out_dir=tmp_path)
    snap = json.loads((tmp_path / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
    for key in ("adr_gross", "sold_room_nights", "available_room_nights",
               "occupancy_rate_month", "room_type_daily_occupancy",
               "room_type_occupancy_chart_series", "room_type_revenue_mix",
               "room_type_metrics_warnings"):
        assert key in snap, f"missing field: {key}"
    assert snap["sold_room_nights"] == 1
    # 既存fieldも壊れていないこと
    assert "today_new_booking_count" in snap
    assert "beds24_revenue_gross_stay" in snap
    conn.close()


def test_room_type_metrics_present_across_multiple_months(tmp_path):
    """2026-07/08/09いずれのmonth snapshotでも新fieldが出ること。"""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        BookingRecord(booking_id="1", checkin_date="2026-07-05", checkout_date="2026-07-06",
                     room_id="685761", gross_revenue=8000, status="confirmed").finalize(),
        BookingRecord(booking_id="2", checkin_date="2026-08-05", checkout_date="2026-08-06",
                     room_id="686762", gross_revenue=9000, status="confirmed").finalize(),
        BookingRecord(booking_id="3", checkin_date="2026-09-05", checkout_date="2026-09-06",
                     room_id="686763", gross_revenue=7000, status="confirmed").finalize(),
    ])
    for month in ("2026-07", "2026-08", "2026-09"):
        ctx = monthly.assemble(month, conn)
        sev = {"all_ok": True, "critical": [], "warnings": []}
        out_dir = tmp_path / month
        bi_export.write_all(month, ctx, checks=[], wb_checks=[], severity=sev, out_dir=out_dir)
        snap = json.loads((out_dir / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
        assert snap["sold_room_nights"] == 1
        assert len(snap["room_type_occupancy_chart_series"]) > 0
    conn.close()
