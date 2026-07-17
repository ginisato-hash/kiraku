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
            created_at_raw="", raw_json_path="", guest_name="", room_name="",
            channel="", room_id=""):
    return BookingRecord(
        booking_id=bid, checkin_date=checkin, checkout_date=checkout or checkin,
        gross_revenue=gross, status=status, created_at_raw=created_at_raw,
        raw_json_path=raw_json_path, guest_name=guest_name, room_name=room_name,
        channel=channel, room_id=room_id,
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


# ---------------- 9時台予約が出ない不具合の調査で確認したJST/UTC境界ケース ----------------
# 実データ調査(2026-07-11)の結論: Beds24 bookingTimeはUTC ISO8601("...Z")固定で、
# 変換ロジック自体(_created_date_jst/_created_datetime_jst)は正しくJSTへ変換できている。
# 不具合の実態はGitHub Actionsのschedule triggerが15分間隔通りに発火していなかったこと
# (実行履歴: workflow更新後の全24回中、15分間隔を維持できた形跡なし)であり、
# バックエンドのJST変換ロジックには問題が無かった。回帰防止のため境界値を明示的に固定する。
def test_utc_early_morning_converts_to_jst_same_day_9am():
    """UTC 00:30 => JST 09:30。9時台作成予約が同日today扱いになることを固定する。"""
    assert brl._created_date_jst("2026-07-11T00:30:00Z") == date(2026, 7, 11)
    assert brl._created_datetime_jst("2026-07-11T00:30:00Z").isoformat() == "2026-07-11T09:30:00+09:00"


def test_utc_previous_day_late_night_rolls_over_to_next_day_jst():
    """UTC前日23:30 => JST翌日08:30。日付繰り上がりを固定する。"""
    assert brl._created_date_jst("2026-07-10T23:30:00Z") == date(2026, 7, 11)
    assert brl._created_datetime_jst("2026-07-10T23:30:00Z").isoformat() == "2026-07-11T08:30:00+09:00"


def test_9am_jst_booking_is_counted_as_todays_new_booking():
    """9時台JST作成の予約が本日の新規予約detailsに正しく入ることをend-to-endで固定する
    (実データのbooking_id 89646497、2026-07-11T09:43:51+09:00作成のケースを再現)。"""
    bookings = [_booking("89646497", "2026-07-25", "2026-07-26", gross=31212,
                        created_at_raw="2026-07-11T00:43:51Z")]  # UTC 00:43 = JST 09:43
    result = brl.calculate_today_new_bookings_for_month(
        bookings, "2026-07", date(2026, 7, 11), EXCLUDE)
    assert result["today_new_booking_count"] == 1
    assert result["today_new_booking_details"][0]["booking_id"] == "89646497"
    assert result["today_new_booking_details"][0]["created_at_jst"] == "2026-07-11T09:43:51+09:00"


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
    # cancelled分はそもそもrevenueへ加算されていない(continueで未加算)ため、
    # ここで再度引いてはいけない。非キャンセル予約が無いのでrevenueは0。
    assert result["today_new_booking_revenue"] == 0


def test_same_day_cancelled_does_not_reduce_revenue_from_other_bookings():
    """2026-08の実データで発見されたバグの再現: 同日作成の非キャンセル予約(合計57,978円)と
    別の同日作成・同日キャンセル予約(50,000円)が同じ対象月に混在する場合、
    キャンセル分は非キャンセル予約の売上を減らしてはいけない
    (修正前は 57,978 - 50,000 = 7,978 という誤った値になっていた)。"""
    bookings = [
        _booking("1", "2026-08-04", "2026-08-06", gross=21978,
                 created_at_raw="2026-07-10T04:10:14Z"),
        _booking("2", "2026-08-14", "2026-08-15", gross=36000,
                 created_at_raw="2026-07-09T20:40:14Z"),
        _booking("3", "2026-08-01", "2026-08-02", gross=50000, status="cancelled",
                 created_at_raw="2026-07-10T01:00:00Z"),
    ]
    result = brl.calculate_today_new_bookings_for_month(
        bookings, "2026-08", date(2026, 7, 10), EXCLUDE)
    assert result["today_new_booking_count"] == 2
    assert result["today_new_booking_cancelled_revenue_excluded"] == 50000
    assert result["today_new_booking_revenue"] == 57978
    assert result["today_new_booking_revenue"] == sum(
        d["revenue_for_target_month"] for d in result["today_new_booking_details"])


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
    assert snap["today_new_booking_count_global"] == 1
    assert snap["today_new_booking_revenue_global"] == 20000
    assert snap["today_new_booking_logic_status_global"] == "ok"
    conn.close()


def test_today_new_booking_revenue_not_prorated_across_month_snapshots(tmp_path, monkeypatch):
    """グローバルサマリーは月選択に依らないため、月をまたぐ予約でも按分せず同じ満額が
    どちらの月別snapshotにも表示される(月別ダッシュボード側の按分ロジックとは無関係)。"""
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
    assert snap_aug["today_new_booking_revenue_global"] == 90000

    ctx_sep = monthly.assemble("2026-09", conn)
    bi_export.write_all("2026-09", ctx_sep, checks=[], wb_checks=[], severity=sev,
                        out_dir=tmp_path / "sep")
    snap_sep = json.loads((tmp_path / "sep" / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
    assert snap_sep["today_new_booking_revenue_global"] == 90000
    conn.close()


def test_daily_global_summary_identical_across_at_least_three_month_snapshots(tmp_path, monkeypatch):
    """daily_global_summary(single source of truth)は月選択に依らないため、
    同一refresh run内では3か月分すべてのsnapshotで完全に同じ値を持つ。"""
    monkeypatch.setattr(brl, "jst_today", lambda: date(2026, 7, 8))
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", gross=10000, created_at_raw="2026-07-08T01:00:00Z"),
        _booking("2", "2026-08-15", "2026-08-16", gross=20000, created_at_raw="2026-07-07T01:00:00Z"),
    ])
    sev = {"all_ok": True, "critical": [], "warnings": []}

    summaries = {}
    for month in ("2026-07", "2026-08", "2026-09"):
        ctx = monthly.assemble(month, conn)
        bi_export.write_all(month, ctx, checks=[], wb_checks=[], severity=sev, out_dir=tmp_path / month)
        snap = json.loads((tmp_path / month / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
        assert "daily_global_summary" in snap
        summaries[month] = snap["daily_global_summary"]

    base = summaries["2026-07"]
    for month in ("2026-08", "2026-09"):
        assert summaries[month] == base, f"daily_global_summary differs: 2026-07 vs {month}"
    for key in ("today_new_bookings", "yesterday_new_bookings", "today_checkins"):
        assert base[key]["status"] == "ok"
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
    assert ctx["today_global_summary"]["today_jst"] == "2026-07-08"
    conn.close()


def test_today_jst_override_via_monthly_assemble_2026_07_09(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    ctx = monthly.assemble("2026-07", conn, today_jst=date(2026, 7, 9))
    assert ctx["today_global_summary"]["today_jst"] == "2026-07-09"
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


# ---------------- 予約経路(OTA)・部屋変更履歴のdetails反映 ----------------
def test_details_include_ota_name_and_raw_source():
    bookings = [_booking("1", "2026-07-10", "2026-07-11",
                         created_at_raw="2026-07-08T01:00:00Z", channel="じゃらんnet")]
    result = brl.calculate_today_new_bookings_for_month(bookings, "2026-07", date(2026, 7, 8), EXCLUDE)
    detail = result["today_new_booking_details"][0]
    assert detail["ota_name"] == "じゃらん"
    assert detail["booking_source_raw"] == "じゃらんnet"


def test_details_default_ota_name_is_direct_when_channel_missing():
    bookings = [_booking("1", "2026-07-10", "2026-07-11", created_at_raw="2026-07-08T01:00:00Z")]
    result = brl.calculate_today_new_bookings_for_month(bookings, "2026-07", date(2026, 7, 8), EXCLUDE)
    detail = result["today_new_booking_details"][0]
    assert detail["ota_name"] == "Direct"


def test_details_include_room_id_and_room_change_history_status():
    bookings = [_booking("1", "2026-07-10", "2026-07-11",
                         created_at_raw="2026-07-08T01:00:00Z", room_id="685761")]
    result = brl.calculate_today_new_bookings_for_month(bookings, "2026-07", date(2026, 7, 8), EXCLUDE)
    detail = result["today_new_booking_details"][0]
    assert detail["room_id"] == "685761"
    assert detail["room_change_history_status"] == "not_available"
    assert detail["room_change_history"] == []


def test_details_new_fields_are_not_pii():
    bookings = [_booking("1", "2026-07-10", "2026-07-11", created_at_raw="2026-07-08T01:00:00Z",
                         channel="じゃらんnet", room_id="685761")]
    result = brl.calculate_today_new_bookings_for_month(bookings, "2026-07", date(2026, 7, 8), EXCLUDE)
    detail = result["today_new_booking_details"][0]
    forbidden_keys = {"email", "phone", "address", "message", "notes", "passport",
                      "invoiceItems", "raw", "raw_json_path", "comments", "firstName", "lastName"}
    assert forbidden_keys.isdisjoint(detail.keys())


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
    assert "today_new_booking_details_global" in snap
    assert snap["today_new_booking_details_global"][0]["guest_name"] == "Suzuki Hanako"
    conn.close()


def test_snapshot_details_include_room_type_via_monthly_assemble(tmp_path):
    """monthly.assemble()側でroom_type_metrics.classify_room_type()を再利用して
    room_typeが付与されること(room_idは実configの実データroom_id)。"""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", gross=10000, created_at_raw="2026-07-08T01:00:00Z",
                guest_name="Suzuki Hanako", channel="じゃらんnet", room_id="685761"),
    ])
    ctx = monthly.assemble("2026-07", conn, today_jst=date(2026, 7, 8))
    sev = {"all_ok": True, "critical": [], "warnings": []}
    bi_export.write_all("2026-07", ctx, checks=[], wb_checks=[], severity=sev, out_dir=tmp_path)
    snap = json.loads((tmp_path / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
    detail = snap["today_new_booking_details_global"][0]
    assert detail["ota_name"] == "じゃらん"
    assert detail["room_id"] == "685761"
    assert detail["room_type_key"] == "single_toilet"
    assert detail["room_type"] == "シングル｜客室トイレ付"
    assert detail["room_change_history_status"] == "not_available"
    assert detail["room_change_history"] == []
    conn.close()


def test_snapshot_details_unknown_room_id_classified_as_unknown(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", gross=10000, created_at_raw="2026-07-08T01:00:00Z",
                room_id="999999999"),
    ])
    ctx = monthly.assemble("2026-07", conn, today_jst=date(2026, 7, 8))
    sev = {"all_ok": True, "critical": [], "warnings": []}
    bi_export.write_all("2026-07", ctx, checks=[], wb_checks=[], severity=sev, out_dir=tmp_path)
    snap = json.loads((tmp_path / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
    detail = snap["today_new_booking_details_global"][0]
    assert detail["room_type_key"] == "unknown"


def test_revenue_equals_details_sum_still_holds_with_new_fields(tmp_path):
    """今回のOTA/部屋タイプ拡張後もtoday_new_booking_revenue_global == sum(details)の不変条件を維持する。"""
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", gross=10000, created_at_raw="2026-07-08T01:00:00Z",
                channel="じゃらんnet", room_id="685761"),
        _booking("2", "2026-07-15", "2026-07-16", gross=20000, created_at_raw="2026-07-08T02:00:00Z",
                channel="Booking.com", room_id="686762"),
    ])
    ctx = monthly.assemble("2026-07", conn, today_jst=date(2026, 7, 8))
    sev = {"all_ok": True, "critical": [], "warnings": []}
    bi_export.write_all("2026-07", ctx, checks=[], wb_checks=[], severity=sev, out_dir=tmp_path)
    snap = json.loads((tmp_path / "bi" / "bi_snapshot.json").read_text(encoding="utf-8"))
    details = snap["today_new_booking_details_global"]
    assert snap["today_new_booking_count_global"] == len(details) == 2
    assert snap["today_new_booking_revenue_global"] == sum(d["revenue"] for d in details)
    conn.close()


# ---------------- 現地決済加算のtoday new booking反映 ----------------
def test_today_new_booking_revenue_includes_onsite_payment_addition(tmp_path):
    raw_path = tmp_path / "2026-07.json"
    raw_path.write_text(
        '[{"id": "1", "status": "confirmed", "price": 10000, "invoiceItems": '
        '[{"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},'
        ' {"type": "charge", "description": "現地決済追加", "lineTotal": 3000}]}]',
        encoding="utf-8")
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", gross=10000,
                created_at_raw="2026-07-08T01:00:00Z", raw_json_path=str(raw_path)),
    ])
    result = brl.calculate_today_new_bookings_for_month(
        db.load_objects(conn, "beds24_bookings"), "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_onsite_payment_revenue"] == 3000
    assert result["today_new_booking_revenue"] == 13000
    detail = result["today_new_booking_details"][0]
    assert detail["onsite_payment_revenue_for_target_month"] == 3000
    assert detail["revenue_for_target_month"] == 13000
    conn.close()


def test_today_new_booking_revenue_zero_onsite_when_payment_method_only(tmp_path):
    raw_path = tmp_path / "2026-07.json"
    raw_path.write_text(
        '[{"id": "1", "status": "confirmed", "price": 13000, "invoiceItems": '
        '[{"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 13000},'
        ' {"type": "payment", "description": "現地支払い", "lineTotal": 0}]}]',
        encoding="utf-8")
    conn = db.connect(tmp_path / "t.sqlite")
    db.upsert(conn, "beds24_bookings", [
        _booking("1", "2026-07-10", "2026-07-11", gross=13000,
                created_at_raw="2026-07-08T01:00:00Z", raw_json_path=str(raw_path)),
    ])
    result = brl.calculate_today_new_bookings_for_month(
        db.load_objects(conn, "beds24_bookings"), "2026-07", date(2026, 7, 8), EXCLUDE)
    assert result["today_new_booking_onsite_payment_revenue"] == 0
    assert result["today_new_booking_revenue"] == 13000
    conn.close()


# ---------------- price=0 fallback(手動作成予約)のtoday new booking反映 ----------------
def test_today_new_booking_counts_price_zero_charge_fallback_once():
    """price=0・charge行11800円の手動予約(実データbooking_id 89381508相当)が
    本日の新規予約に一度だけ計上され、二重計上されないこと。"""
    raw = {
        "id": "89381508", "apiSource": "Direct", "status": "confirmed", "price": 0,
        "arrival": "2026-07-06", "departure": "2026-07-08",
        "bookingTime": "2026-07-06T01:43:27Z",
        "invoiceItems": [{"type": "charge", "description": "", "lineTotal": 11800}],
    }
    rec = normalize_booking(raw)
    result = brl.calculate_today_new_bookings_for_month(
        [rec], "2026-07", date(2026, 7, 6), EXCLUDE)
    assert result["today_new_booking_count"] == 1
    assert result["today_new_booking_revenue"] == 11800
    assert result["today_new_booking_details"][0]["revenue_for_target_month"] == 11800


def test_today_new_booking_price_zero_fallback_prorates_across_months():
    """price=0・charge合計30,000円、8/30〜9/2(3泊: 8月2泊/9月1泊)の月跨ぎ予約が
    fallback適用後も按分ロジックで正しく分割されること。"""
    raw = {
        "id": "1", "status": "confirmed", "price": 0,
        "arrival": "2026-08-30", "departure": "2026-09-02",
        "bookingTime": "2026-07-08T01:00:00Z",
        "invoiceItems": [{"type": "charge", "description": "", "lineTotal": 30000}],
    }
    rec = normalize_booking(raw)
    aug = brl.calculate_today_new_bookings_for_month([rec], "2026-08", date(2026, 7, 8), EXCLUDE)
    sep = brl.calculate_today_new_bookings_for_month([rec], "2026-09", date(2026, 7, 8), EXCLUDE)
    assert aug["today_new_booking_revenue"] == 20000
    assert sep["today_new_booking_revenue"] == 10000
