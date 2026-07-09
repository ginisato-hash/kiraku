"""「本日の新規予約」件数・金額の計算テスト（Beds24 bookingTime基準、JST判定）。"""
import json
from datetime import date

from yuge_finance import config, db, monthly
from yuge_finance.accounting import beds24_revenue_logic as brl
from yuge_finance.api.beds24_client import normalize_booking
from yuge_finance.normalize.schema import BookingRecord
from yuge_finance.reports import bi_export

EXCLUDE = ["cancelled", "canceled", "black"]


def _booking(bid, checkin, checkout=None, gross=10000, status="confirmed",
            created_at_raw="", raw_json_path="", guest_name="", room_name=""):
    return BookingRecord(
        booking_id=bid, checkin_date=checkin, checkout_date=checkout or checkin,
        gross_revenue=gross, status=status, created_at_raw=created_at_raw,
        raw_json_path=raw_json_path, guest_name=guest_name, room_name=room_name,
    ).finalize()


# ---------------- created date extraction ----------------
def test_normalize_booking_extracts_booking_time_as_created_at_raw():
    raw = {"id": "1", "bookingTime": "2026-07-08T12:01:31Z", "arrival": "2026-07-10",
          "departure": "2026-07-11", "price": 10000, "status": "confirmed"}
    rec = normalize_booking(raw)
    assert rec.created_at_raw == "2026-07-08T12:01:31Z"


def test_created_date_jst_converts_utc_to_jst_correctly():
    # 2026-07-08T16:00:00Z(UTC) = 2026-07-09 01:00 JST -> 日付は07-09に繰り上がる
    assert brl._created_date_jst("2026-07-08T16:00:00Z") == date(2026, 7, 9)
    assert brl._created_date_jst("2026-07-08T10:00:00Z") == date(2026, 7, 8)


def test_created_date_jst_returns_none_when_missing_or_unparseable():
    assert brl._created_date_jst("") is None
    assert brl._created_date_jst(None) is None
    assert brl._created_date_jst("not-a-date") is None


def test_logic_status_is_field_missing_when_no_booking_has_created_at():
    bookings = [_booking("1", "2026-07-10", "2026-07-11", created_at_raw="")]
    result = brl.calculate_today_new_bookings_for_month(
        bookings, "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_logic_status"] == "created_at_field_missing"
    assert result["today_new_booking_count"] == 0


# ---------------- today new booking count ----------------
def test_booking_created_today_is_counted():
    bookings = [_booking("1", "2026-07-10", "2026-07-11",
                         created_at_raw="2026-07-08T03:00:00Z")]
    result = brl.calculate_today_new_bookings_for_month(
        bookings, "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_count"] == 1
    assert result["today_new_booking_logic_status"] == "ok"


def test_booking_created_yesterday_is_not_counted():
    bookings = [_booking("1", "2026-07-10", "2026-07-11",
                         created_at_raw="2026-07-07T03:00:00Z")]
    result = brl.calculate_today_new_bookings_for_month(
        bookings, "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_count"] == 0


def test_booking_created_in_future_is_not_counted():
    bookings = [_booking("1", "2026-07-10", "2026-07-11",
                         created_at_raw="2026-07-09T03:00:00Z")]
    result = brl.calculate_today_new_bookings_for_month(
        bookings, "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_count"] == 0


def test_cancelled_booking_excluded_from_count():
    bookings = [_booking("1", "2026-07-10", "2026-07-11", status="cancelled",
                         created_at_raw="2026-06-01T03:00:00Z")]
    result = brl.calculate_today_new_bookings_for_month(
        bookings, "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_count"] == 0


def test_same_day_created_and_cancelled_goes_to_cancelled_excluded():
    bookings = [_booking("1", "2026-07-10", "2026-07-11", gross=15000, status="cancelled",
                         created_at_raw="2026-07-08T03:00:00Z")]
    result = brl.calculate_today_new_bookings_for_month(
        bookings, "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_count"] == 0
    assert result["today_new_booking_cancelled_count"] == 1
    assert result["today_new_booking_cancelled_revenue_excluded"] == 15000
    assert result["today_new_booking_revenue"] == -15000  # gross(0) + point(0) - excluded(15000)


# ---------------- month allocation ----------------
def test_booking_not_overlapping_target_month_is_excluded():
    bookings = [_booking("1", "2026-09-01", "2026-09-02",
                         created_at_raw="2026-07-08T03:00:00Z")]
    result = brl.calculate_today_new_bookings_for_month(
        bookings, "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_count"] == 0


def test_cross_month_booking_counted_in_both_months():
    bookings = [_booking("1", "2026-08-30", "2026-09-02", gross=90000,
                         created_at_raw="2026-07-08T03:00:00Z")]
    aug = brl.calculate_today_new_bookings_for_month(bookings, "2026-08", date(2026, 7, 8), EXCLUDE)
    sep = brl.calculate_today_new_bookings_for_month(bookings, "2026-09", date(2026, 7, 8), EXCLUDE)
    assert aug["today_new_booking_count"] == 1
    assert sep["today_new_booking_count"] == 1


def test_cross_month_revenue_prorated_by_nights():
    """90,000円・2026-08-30〜09-02(3泊、8月2泊/9月1泊)は 60,000/30,000 に按分される。"""
    bookings = [_booking("1", "2026-08-30", "2026-09-02", gross=90000,
                         created_at_raw="2026-07-08T03:00:00Z")]
    aug = brl.calculate_today_new_bookings_for_month(bookings, "2026-08", date(2026, 7, 8), EXCLUDE)
    sep = brl.calculate_today_new_bookings_for_month(bookings, "2026-09", date(2026, 7, 8), EXCLUDE)
    assert aug["today_new_booking_gross_stay_revenue"] == 60000
    assert aug["today_new_booking_revenue"] == 60000
    assert sep["today_new_booking_gross_stay_revenue"] == 30000
    assert sep["today_new_booking_revenue"] == 30000


def test_multiple_bookings_aggregate_count_and_revenue():
    bookings = [
        _booking("1", "2026-07-10", "2026-07-11", gross=20000, created_at_raw="2026-07-08T01:00:00Z"),
        _booking("2", "2026-07-15", "2026-07-16", gross=30000, created_at_raw="2026-07-08T02:00:00Z"),
        _booking("3", "2026-07-20", "2026-07-21", gross=40000, created_at_raw="2026-07-01T02:00:00Z"),
    ]
    result = brl.calculate_today_new_bookings_for_month(bookings, "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_count"] == 2
    assert result["today_new_booking_revenue"] == 50000


# ---------------- snapshot integration ----------------
def test_today_new_booking_fields_present_in_bi_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(brl, "jst_today", lambda: date(2026, 7, 8))
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", gross=20000,
                created_at_raw="2026-07-08T01:00:00Z"),
    ])
    ctx = monthly.assemble("2026-07", conn)
    sev = {"all_ok": True, "critical": [], "warnings": []}
    bi_export.write_all("2026-07", ctx, checks=[], wb_checks=[], severity=sev, out_dir=tmp_path)
    snap = json.loads((tmp_path / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
    assert snap["today_new_booking_count"] == 1
    assert snap["today_new_booking_revenue"] == 20000
    assert snap["today_new_booking_logic_status"] == "ok"
    conn.close()


def test_today_new_booking_prorates_across_months_in_snapshot(tmp_path, monkeypatch):
    """月をまたぐ予約が両方の月別snapshotに按分計上される。"""
    monkeypatch.setattr(brl, "jst_today", lambda: date(2026, 7, 8))
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-08-30", "2026-09-02", gross=90000,
                created_at_raw="2026-07-08T01:00:00Z"),
    ])
    sev = {"all_ok": True, "critical": [], "warnings": []}

    ctx_aug = monthly.assemble("2026-08", conn)
    bi_export.write_all("2026-08", ctx_aug, checks=[], wb_checks=[], severity=sev,
                        out_dir=tmp_path / "aug")
    snap_aug = json.loads((tmp_path / "aug" / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
    assert snap_aug["today_new_booking_revenue"] == 60000

    ctx_sep = monthly.assemble("2026-09", conn)
    bi_export.write_all("2026-09", ctx_sep, checks=[], wb_checks=[], severity=sev,
                        out_dir=tmp_path / "sep")
    snap_sep = json.loads((tmp_path / "sep" / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
    assert snap_sep["today_new_booking_revenue"] == 30000
    conn.close()


# ---------------- 日付跨ぎ更新不具合対応: today_jst override / generated_at_jst ----------------
def test_snapshot_has_explicit_today_jst_field(tmp_path):
    """today_jstをsnapshotに明示出力する（日付跨ぎ検証のため必須）。"""
    monkeypatch_free_conn = db.connect(tmp_path / "t.sqlite")
    ctx = monthly.assemble("2026-07", monkeypatch_free_conn, today_jst=date(2026, 7, 8))
    sev = {"all_ok": True, "critical": [], "warnings": []}
    bi_export.write_all("2026-07", ctx, checks=[], wb_checks=[], severity=sev, out_dir=tmp_path)
    snap = json.loads((tmp_path / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
    assert snap["today_jst"] == "2026-07-08"
    monkeypatch_free_conn.close()


def test_snapshot_has_generated_at_jst_field(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    ctx = monthly.assemble("2026-07", conn, today_jst=date(2026, 7, 8))
    sev = {"all_ok": True, "critical": [], "warnings": []}
    bi_export.write_all("2026-07", ctx, checks=[], wb_checks=[], severity=sev, out_dir=tmp_path)
    snap = json.loads((tmp_path / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
    assert snap.get("generated_at_jst")
    conn.close()


def test_today_jst_override_via_monthly_assemble_2026_07_08(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    ctx = monthly.assemble("2026-07", conn, today_jst=date(2026, 7, 8))
    assert ctx["today_new_bookings"]["today_jst"] == "2026-07-08"
    conn.close()


def test_today_jst_override_via_monthly_assemble_2026_07_09(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    ctx = monthly.assemble("2026-07", conn, today_jst=date(2026, 7, 9))
    assert ctx["today_new_bookings"]["today_jst"] == "2026-07-09"
    conn.close()


def test_today_jst_override_via_cli_refresh(tmp_path, monkeypatch):
    """--today-jst 相当のbi_refresh.refresh(today_jst_override=...) が正しく効く。"""
    from yuge_finance import bi_refresh
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(bi_refresh, "_fetch_beds24", lambda month, conn: 0)
    conn = db.connect(tmp_path / "ledger.sqlite")
    status = bi_refresh.refresh(["2026-07"], conn=conn, today_jst_override="2026-07-08")
    assert status["ok"] is True
    conn.close()


def test_day_rollover_resets_today_new_booking_count(tmp_path):
    """日付が変わったら、本日の新規予約は新しいJST日付で0から再計算される。

    booking A created_at=2026-07-08T23:50:00+09:00 (=2026-07-08T14:50:00Z)
    booking B created_at=2026-07-09T00:05:00+09:00 (=2026-07-08T15:05:00Z)
    target_month=2026-07
    """
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("A", "2026-07-15", "2026-07-16", gross=10000,
                created_at_raw="2026-07-08T14:50:00Z"),
        _booking("B", "2026-07-20", "2026-07-21", gross=20000,
                created_at_raw="2026-07-08T15:05:00Z"),
    ])

    result_day1 = brl.calculate_today_new_bookings_for_month(
        db.load_objects(conn, "beds24_bookings"), "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result_day1["today_new_booking_count"] == 1
    assert result_day1["today_new_booking_ids_sample"] == ["A"]

    result_day2 = brl.calculate_today_new_bookings_for_month(
        db.load_objects(conn, "beds24_bookings"), "2026-07", date(2026, 7, 9), EXCLUDE)
    assert result_day2["today_new_booking_count"] == 1
    assert result_day2["today_new_booking_ids_sample"] == ["B"]
    conn.close()


# ---------------- 本日新規予約 詳細一覧 (today_new_booking_details) ----------------
def test_details_include_checkin_checkout_guest_name_and_revenue(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-12", gross=24000, created_at_raw="2026-07-08T01:00:00Z",
                guest_name="Yamada Taro", room_name="201"),
    ])
    result = brl.calculate_today_new_bookings_for_month(
        db.load_objects(conn, "beds24_bookings"), "2026-07", date(2026, 7, 8), EXCLUDE)
    assert len(result["today_new_booking_details"]) == 1
    d = result["today_new_booking_details"][0]
    assert d["checkin"] == "2026-07-10"
    assert d["checkout"] == "2026-07-12"
    assert d["guest_name"] == "Yamada Taro"
    assert d["revenue_for_target_month"] == 24000
    assert d["room_name"] == "201"
    assert d["total_nights"] == 2
    assert d["target_month_nights"] == 2
    assert d["created_at_jst"] == "2026-07-08T10:00:00+09:00"
    conn.close()


def test_details_missing_guest_name_defaults_to_placeholder(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", created_at_raw="2026-07-08T01:00:00Z", guest_name=""),
    ])
    result = brl.calculate_today_new_bookings_for_month(
        db.load_objects(conn, "beds24_bookings"), "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_details"][0]["guest_name"] == "氏名未取得"
    conn.close()


def test_details_never_contain_pii_keys(tmp_path):
    """detail dictにはemail/phone/address/message/notes/passport等のキーを一切持たせない。"""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", created_at_raw="2026-07-08T01:00:00Z",
                guest_name="Yamada Taro"),
    ])
    result = brl.calculate_today_new_bookings_for_month(
        db.load_objects(conn, "beds24_bookings"), "2026-07", date(2026, 7, 8), EXCLUDE)
    detail = result["today_new_booking_details"][0]
    forbidden_keys = {"email", "phone", "address", "message", "notes", "passport",
                      "invoiceItems", "raw", "raw_json_path", "comments", "firstName", "lastName"}
    assert forbidden_keys.isdisjoint(detail.keys())
    conn.close()


def test_details_count_matches_today_new_booking_count(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", gross=10000, created_at_raw="2026-07-08T01:00:00Z"),
        _booking("2", "2026-07-15", "2026-07-16", gross=15000, created_at_raw="2026-07-08T02:00:00Z"),
        _booking("3", "2026-07-20", "2026-07-21", gross=5000, created_at_raw="2026-07-07T02:00:00Z"),  # 前日作成
    ])
    result = brl.calculate_today_new_bookings_for_month(
        db.load_objects(conn, "beds24_bookings"), "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_count"] == len(result["today_new_booking_details"]) == 2
    conn.close()


def test_details_revenue_sum_matches_today_new_booking_revenue(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", gross=10000, created_at_raw="2026-07-08T01:00:00Z"),
        _booking("2", "2026-07-15", "2026-07-16", gross=15000, created_at_raw="2026-07-08T02:00:00Z"),
    ])
    result = brl.calculate_today_new_bookings_for_month(
        db.load_objects(conn, "beds24_bookings"), "2026-07", date(2026, 7, 8), EXCLUDE)
    detail_sum = sum(d["revenue_for_target_month"] for d in result["today_new_booking_details"])
    assert detail_sum == result["today_new_booking_revenue"] == 25000
    conn.close()


def test_details_prorated_correctly_for_month_crossing_booking(tmp_path):
    """予約総額90,000、2026-08-30〜2026-09-02(total 3泊: 08=2泊,09=1泊)。"""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-08-30", "2026-09-02", gross=90000, created_at_raw="2026-07-08T01:00:00Z"),
    ])
    bookings = db.load_objects(conn, "beds24_bookings")

    result_aug = brl.calculate_today_new_bookings_for_month(bookings, "2026-08", date(2026, 7, 8), EXCLUDE)
    d_aug = result_aug["today_new_booking_details"][0]
    assert d_aug["revenue_for_target_month"] == 60000
    assert d_aug["target_month_nights"] == 2
    assert d_aug["total_nights"] == 3
    assert d_aug["total_booking_revenue"] == 90000

    result_sep = brl.calculate_today_new_bookings_for_month(bookings, "2026-09", date(2026, 7, 8), EXCLUDE)
    d_sep = result_sep["today_new_booking_details"][0]
    assert d_sep["revenue_for_target_month"] == 30000
    assert d_sep["target_month_nights"] == 1
    conn.close()


def test_details_excludes_bookings_not_overlapping_target_month(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-09-10", "2026-09-12", gross=10000, created_at_raw="2026-07-08T01:00:00Z"),
    ])
    result = brl.calculate_today_new_bookings_for_month(
        db.load_objects(conn, "beds24_bookings"), "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_details"] == []
    assert result["today_new_booking_count"] == 0
    conn.close()


def test_details_excludes_cancelled_bookings(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", gross=10000, status="cancelled",
                created_at_raw="2026-07-08T01:00:00Z"),
    ])
    result = brl.calculate_today_new_bookings_for_month(
        db.load_objects(conn, "beds24_bookings"), "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_details"] == []
    assert result["today_new_booking_cancelled_count"] == 1


def test_details_excludes_same_day_created_and_cancelled_booking(tmp_path):
    """同日作成・同日キャンセルはcancelled_countに入るがdetailsには出ない。"""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", gross=10000, status="confirmed",
                created_at_raw="2026-07-08T01:00:00Z"),
        _booking("2", "2026-07-12", "2026-07-13", gross=20000, status="cancelled",
                created_at_raw="2026-07-08T02:00:00Z"),
    ])
    result = brl.calculate_today_new_bookings_for_month(
        db.load_objects(conn, "beds24_bookings"), "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_count"] == 1
    assert len(result["today_new_booking_details"]) == 1
    assert result["today_new_booking_details"][0]["booking_id"] == "1"
    assert result["today_new_booking_cancelled_count"] == 1


def test_snapshot_includes_today_new_booking_details(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", gross=10000, created_at_raw="2026-07-08T01:00:00Z",
                guest_name="Suzuki Hanako"),
    ])
    ctx = monthly.assemble("2026-07", conn, today_jst=date(2026, 7, 8))
    sev = {"all_ok": True, "critical": [], "warnings": []}
    bi_export.write_all("2026-07", ctx, checks=[], wb_checks=[], severity=sev, out_dir=tmp_path)
    snap = json.loads((tmp_path / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
    assert "today_new_booking_details" in snap
    assert snap["today_new_booking_details"][0]["guest_name"] == "Suzuki Hanako"
    conn.close()
