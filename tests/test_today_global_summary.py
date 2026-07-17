"""calculate_today_global_summary: 月選択に依らないグローバルな「本日」サマリー。

対象月フィルタ・按分を一切行わない点が calculate_today_new_bookings_for_month との違い。
  - 新規予約: JST今日作成された非キャンセル予約を、チェックイン月を問わず予約単位の
    総売上額(price)でそのまま合算する。
  - 本日チェックイン: チェックイン日がJST今日の非キャンセル予約を、作成日を問わず
    同様に合算する。
"""
import json
from datetime import date

from yuge_finance.accounting import beds24_revenue_logic as brl
from yuge_finance.api.beds24_client import normalize_booking

EXCLUDE = ["cancelled", "canceled", "black"]
TODAY = date(2026, 7, 11)


def _raw_booking(bid, price, checkin, checkout, status="new", room_id="686764"):
    return {
        "id": bid, "status": status, "price": price, "roomId": room_id,
        "arrival": checkin, "departure": checkout,
        "invoiceItems": [{"type": "charge", "description": "[ROOMNAME1]", "lineTotal": price}],
    }


def _write_raw(tmp_path, name, bookings_raw):
    p = tmp_path / name
    p.write_text(json.dumps(bookings_raw), encoding="utf-8")
    return str(p)


def _rec(raw, raw_path, created_at_raw=""):
    r = normalize_booking(raw)
    r.raw_json_path = raw_path
    r.created_at_raw = created_at_raw
    r.finalize()
    return r


def test_new_booking_counts_full_revenue_regardless_of_checkin_month(tmp_path):
    """今日作成・来月チェックインの予約でも、対象月フィルタ無しで満額計上される。"""
    raw = _raw_booking("1", 31212, "2026-09-25", "2026-09-26")
    raw_path = _write_raw(tmp_path, "raw.json", [raw])
    rec = _rec(raw, raw_path, "2026-07-11T00:43:51Z")  # JST 09:43
    result = brl.calculate_today_global_summary([rec], TODAY, EXCLUDE)
    assert result["today_new_booking_count_global"] == 1
    assert result["today_new_booking_revenue_global"] == 31212
    assert result["today_new_booking_details_global"][0]["revenue"] == 31212
    # チェックインは来月なので本日チェックインには計上されない
    assert result["today_checkin_count_global"] == 0
    assert result["today_checkin_revenue_global"] == 0


def test_checkin_today_counts_regardless_of_when_created(tmp_path):
    """作成日が今日でなくても、チェックイン日が今日なら本日チェックインに計上される。"""
    raw = _raw_booking("2", 15000, "2026-07-11", "2026-07-12")
    raw_path = _write_raw(tmp_path, "raw.json", [raw])
    rec = _rec(raw, raw_path, "2026-06-01T00:00:00Z")  # 1ヶ月以上前に作成
    result = brl.calculate_today_global_summary([rec], TODAY, EXCLUDE)
    assert result["today_checkin_count_global"] == 1
    assert result["today_checkin_revenue_global"] == 15000
    assert result["today_checkin_details_global"][0]["revenue"] == 15000
    # 今日作成ではないので新規予約には計上されない
    assert result["today_new_booking_count_global"] == 0
    assert result["today_new_booking_revenue_global"] == 0


def test_same_day_created_and_checkin_appears_in_both_metrics(tmp_path):
    """当日作成・当日チェックインの予約は、2つの独立した指標の両方に計上される(二重計上ではない)。"""
    raw = _raw_booking("3", 9000, "2026-07-11", "2026-07-12")
    raw_path = _write_raw(tmp_path, "raw.json", [raw])
    rec = _rec(raw, raw_path, "2026-07-11T00:00:00Z")
    result = brl.calculate_today_global_summary([rec], TODAY, EXCLUDE)
    assert result["today_new_booking_count_global"] == 1
    assert result["today_new_booking_revenue_global"] == 9000
    assert result["today_checkin_count_global"] == 1
    assert result["today_checkin_revenue_global"] == 9000


def test_cancelled_bookings_excluded_from_both_metrics(tmp_path):
    raw = _raw_booking("4", 20000, "2026-07-11", "2026-07-12", status="cancelled")
    raw_path = _write_raw(tmp_path, "raw.json", [raw])
    rec = _rec(raw, raw_path, "2026-07-11T00:00:00Z")
    result = brl.calculate_today_global_summary([rec], TODAY, EXCLUDE)
    assert result["today_new_booking_count_global"] == 0
    assert result["today_checkin_count_global"] == 0


def test_multiple_new_bookings_across_different_checkin_months_sum_without_proration(tmp_path):
    raws = [
        _raw_booking("10", 10000, "2026-07-15", "2026-07-16"),
        _raw_booking("11", 30000, "2026-08-30", "2026-09-02"),  # 月跨ぎでも按分しない
    ]
    raw_path = _write_raw(tmp_path, "raw.json", raws)
    recs = [_rec(r, raw_path, "2026-07-11T00:00:00Z") for r in raws]
    result = brl.calculate_today_global_summary(recs, TODAY, EXCLUDE)
    assert result["today_new_booking_count_global"] == 2
    assert result["today_new_booking_revenue_global"] == 10000 + 30000
    assert result["today_new_booking_revenue_global"] == sum(
        d["revenue"] for d in result["today_new_booking_details_global"])


def test_created_at_field_missing_when_no_booking_has_created_at(tmp_path):
    raw = _raw_booking("5", 10000, "2026-07-11", "2026-07-12")
    raw_path = _write_raw(tmp_path, "raw.json", [raw])
    rec = _rec(raw, raw_path, "")  # created_at_raw無し
    result = brl.calculate_today_global_summary([rec], TODAY, EXCLUDE)
    assert result["today_new_booking_logic_status_global"] == "created_at_field_missing"
    # チェックイン側はcreated_atに依存しないため、判定不可の影響を受けない
    assert result["today_checkin_count_global"] == 1


# ---------------- 前日の新規予約 ----------------
def test_yesterday_new_booking_counted_regardless_of_checkin_month(tmp_path):
    """前日JST作成の予約は、チェックイン月を問わず満額でyesterday側に計上される。"""
    raw = _raw_booking("20", 20000, "2026-09-01", "2026-09-02")
    raw_path = _write_raw(tmp_path, "raw.json", [raw])
    rec = _rec(raw, raw_path, "2026-07-10T00:00:00Z")  # JST前日(2026-07-10)作成
    result = brl.calculate_today_global_summary([rec], TODAY, EXCLUDE)
    assert result["yesterday_new_booking_count_global"] == 1
    assert result["yesterday_new_booking_revenue_global"] == 20000
    assert result["yesterday_new_booking_details_global"][0]["revenue"] == 20000
    # 今日作成でも本日チェックインでもないので他の2指標には計上されない
    assert result["today_new_booking_count_global"] == 0
    assert result["today_checkin_count_global"] == 0


def test_yesterday_new_booking_excludes_cancelled(tmp_path):
    raw = _raw_booking("21", 20000, "2026-07-10", "2026-07-11", status="cancelled")
    raw_path = _write_raw(tmp_path, "raw.json", [raw])
    rec = _rec(raw, raw_path, "2026-07-10T00:00:00Z")
    result = brl.calculate_today_global_summary([rec], TODAY, EXCLUDE)
    assert result["yesterday_new_booking_count_global"] == 0
    assert result["yesterday_new_booking_revenue_global"] == 0


def test_today_and_yesterday_new_bookings_are_mutually_exclusive(tmp_path):
    """今日作成の予約はyesterday側に、前日作成の予約はtoday側に混入しない。"""
    today_raw = _raw_booking("30", 10000, "2026-07-20", "2026-07-21")
    yesterday_raw = _raw_booking("31", 20000, "2026-07-20", "2026-07-21")
    raw_path = _write_raw(tmp_path, "raw.json", [today_raw, yesterday_raw])
    today_rec = _rec(today_raw, raw_path, "2026-07-11T00:00:00Z")
    yesterday_rec = _rec(yesterday_raw, raw_path, "2026-07-10T00:00:00Z")
    result = brl.calculate_today_global_summary([today_rec, yesterday_rec], TODAY, EXCLUDE)
    assert result["today_new_booking_count_global"] == 1
    assert result["today_new_booking_revenue_global"] == 10000
    assert result["yesterday_new_booking_count_global"] == 1
    assert result["yesterday_new_booking_revenue_global"] == 20000


# ---------------- JST境界 ----------------
def test_jst_boundary_utc_evening_previous_day_counts_as_today_jst(tmp_path):
    """created_at=2026-07-17T16:30:00Z はJSTで2026-07-18T01:30:00+09:00になるため、
    today_jst=2026-07-18の「本日の新規予約」に含まれる。"""
    raw = _raw_booking("40", 30000, "2026-09-01", "2026-09-02")
    raw_path = _write_raw(tmp_path, "raw.json", [raw])
    rec = _rec(raw, raw_path, "2026-07-17T16:30:00Z")
    result = brl.calculate_today_global_summary([rec], date(2026, 7, 18), EXCLUDE)
    assert result["today_new_booking_count_global"] == 1
    assert result["today_new_booking_revenue_global"] == 30000


# ---------------- 重複booking_id排除 ----------------
def test_duplicate_booking_id_across_raw_files_counted_once(tmp_path):
    """同一booking_idが複数raw fileの読み込み結果に重複して現れても1件として扱う
    (例: 月またぎ集計でDBロード時に同一予約が複数回現れるケースの安全策)。"""
    raw = _raw_booking("50", 12000, "2026-07-20", "2026-07-21")
    raw_path = _write_raw(tmp_path, "raw.json", [raw])
    rec1 = _rec(raw, raw_path, "2026-07-11T00:00:00Z")
    rec2 = _rec(raw, raw_path, "2026-07-11T00:00:00Z")  # 同じbooking_idの重複レコード
    result = brl.calculate_today_global_summary([rec1, rec2], TODAY, EXCLUDE)
    assert result["today_new_booking_count_global"] == 1
    assert result["today_new_booking_revenue_global"] == 12000


# ---------------- 集計整合(count==len(details), revenue==sum(details)) ----------------
def test_count_and_revenue_are_consistent_with_details_for_all_three_buckets(tmp_path):
    raws = [
        _raw_booking("60", 10000, "2026-07-25", "2026-07-26"),  # today new
        _raw_booking("61", 20000, "2026-08-25", "2026-08-26"),  # yesterday new
        _raw_booking("62", 15000, "2026-07-11", "2026-07-12"),  # today checkin
    ]
    raw_path = _write_raw(tmp_path, "raw.json", raws)
    recs = [
        _rec(raws[0], raw_path, "2026-07-11T00:00:00Z"),
        _rec(raws[1], raw_path, "2026-07-10T00:00:00Z"),
        _rec(raws[2], raw_path, "2026-06-01T00:00:00Z"),
    ]
    result = brl.calculate_today_global_summary(recs, TODAY, EXCLUDE)
    for prefix in ("today_new_booking", "yesterday_new_booking", "today_checkin"):
        count = result[f"{prefix}_count_global"]
        revenue = result[f"{prefix}_revenue_global"]
        details = result[f"{prefix}_details_global"]
        assert count == len(details), prefix
        assert revenue == sum(d["revenue"] for d in details), prefix


# ---------------- 予約ID 89646497 実データケース ----------------
def test_booking_89646497_recognized_revenue_is_price_only(tmp_path):
    """price=31,212 / coupon=8,800 / point=1,000 / 事前決済=21,412 のいずれも売上へ
    別途加算・控除せず、売上=price(31,212)のまま。"""
    raw = {
        "id": "89646497", "status": "new", "price": 31212, "roomId": "686764",
        "arrival": "2026-07-25", "departure": "2026-07-26",
        "invoiceItems": [
            {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 31212},
            {"type": "payment", "description": "coupon", "lineTotal": -8800},
            {"type": "payment", "description": "point", "lineTotal": -1000},
            {"type": "payment", "description": "事前決済", "lineTotal": -21412},
        ],
    }
    raw_path = _write_raw(tmp_path, "raw.json", [raw])
    rec = _rec(raw, raw_path, "2026-07-11T00:00:00Z")
    result = brl.calculate_today_global_summary([rec], TODAY, EXCLUDE)
    assert result["today_new_booking_revenue_global"] == 31212
    assert result["today_new_booking_details_global"][0]["revenue"] == 31212


# ---------------- daily_global_summary(single source of truth) ----------------
def test_daily_global_summary_nested_schema_matches_flat_fields(tmp_path):
    raw = _raw_booking("70", 40000, "2026-07-11", "2026-07-12")
    raw_path = _write_raw(tmp_path, "raw.json", [raw])
    rec = _rec(raw, raw_path, "2026-07-11T00:00:00Z")
    result = brl.calculate_today_global_summary([rec], TODAY, EXCLUDE)
    dgs = result["daily_global_summary"]

    assert set(dgs.keys()) == {"today_new_bookings", "yesterday_new_bookings", "today_checkins"}
    for key in dgs:
        bucket = dgs[key]
        assert bucket["status"] == "ok"
        assert bucket["count"] == len(bucket["details"])
        assert bucket["revenue"] == sum(d["revenue"] for d in bucket["details"])

    assert dgs["today_new_bookings"]["count"] == result["today_new_booking_count_global"]
    assert dgs["today_new_bookings"]["revenue"] == result["today_new_booking_revenue_global"]
    assert dgs["today_new_bookings"]["date_jst"] == TODAY.isoformat()
    assert dgs["yesterday_new_bookings"]["count"] == result["yesterday_new_booking_count_global"]
    assert dgs["yesterday_new_bookings"]["date_jst"] == "2026-07-10"
    assert dgs["today_checkins"]["count"] == result["today_checkin_count_global"]


def test_daily_global_summary_normal_zero_is_ok_not_created_at_field_missing(tmp_path):
    """作成日時fieldが正常に存在する予約群の中で、たまたま今日/前日該当0件でも
    statusは"ok"のまま("判定不可"にはしない)。"""
    raw = _raw_booking("80", 10000, "2026-05-01", "2026-05-02")
    raw_path = _write_raw(tmp_path, "raw.json", [raw])
    rec = _rec(raw, raw_path, "2026-06-01T00:00:00Z")  # 今日でも前日でもない作成日
    result = brl.calculate_today_global_summary([rec], TODAY, EXCLUDE)
    dgs = result["daily_global_summary"]
    assert dgs["today_new_bookings"] == {
        "date_jst": TODAY.isoformat(), "status": "ok", "count": 0, "revenue": 0, "details": [],
        "note": dgs["today_new_bookings"]["note"],
    }
    assert dgs["yesterday_new_bookings"]["status"] == "ok"
    assert dgs["yesterday_new_bookings"]["count"] == 0
    assert dgs["today_checkins"]["status"] == "ok"
    assert dgs["today_checkins"]["count"] == 0
