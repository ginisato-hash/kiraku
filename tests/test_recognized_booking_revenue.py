"""予約別の認識売上額 = price(price=0はcharge fallback済み)そのまま(総額)。

2026-07-11、v4で一時的に「クーポンは施設実質負担のため売上控除」としたが、ユーザーの
最終判断でv5に撤回。coupon/point/banktransfer/事前決済/現地決済はすべてpriceの決済
チャネル内訳に過ぎず、売上へ別途加算・控除しない。couponの月次合計はBI上の参考情報
(beds24_coupon_discount_amount / beds24_coupon_reference_amount)としてのみ表示する。
"""
import json

from yuge_finance import db, monthly
from yuge_finance.accounting import beds24_revenue_logic as brl
from yuge_finance.accounting import revenue_recon, room_type_metrics as rtm
from yuge_finance.api.beds24_client import normalize_booking
from yuge_finance.normalize.schema import BookingRecord

EXCLUDE = ["cancelled", "canceled", "black"]


def _raw_booking(bid, price, checkin="2026-07-25", checkout="2026-07-26",
                 coupon=0, point=0, status="new", room_id="686764"):
    items = [{"type": "charge", "description": "[ROOMNAME1]", "lineTotal": price}]
    if point:
        items.append({"type": "payment", "description": "point", "lineTotal": -point})
    if coupon:
        items.append({"type": "payment", "description": "coupon", "lineTotal": -coupon})
    return {
        "id": bid, "status": status, "price": price, "roomId": room_id,
        "arrival": checkin, "departure": checkout, "invoiceItems": items,
    }


def _write_raw(tmp_path, bookings_raw):
    p = tmp_path / "raw.json"
    p.write_text(json.dumps(bookings_raw), encoding="utf-8")
    return str(p)


# ---------------- calculate_recognized_booking_revenue: 基本ケース ----------------
def test_booking_89646497_real_case_is_gross_price_unaffected_by_coupon_or_point(tmp_path):
    """実データ: price=31,212 / point=1,000 / coupon=8,800 => recognized=31,212(総額)。"""
    raw = _raw_booking("89646497", 31212, coupon=8800, point=1000)
    raw_path = _write_raw(tmp_path, [raw])
    rec = normalize_booking(raw)
    rec.raw_json_path = raw_path
    rec.finalize()
    raw_index = brl._load_raw_index(raw_path)
    assert brl.calculate_recognized_booking_revenue(rec, raw_index) == 31212


def test_coupon_only_not_deducted():
    raw = _raw_booking("1", 10000, coupon=2000)
    rec = normalize_booking(raw)
    raw_index = {"1": raw}
    assert brl.calculate_recognized_booking_revenue(rec, raw_index) == 10000


def test_point_only_not_added():
    raw = _raw_booking("1", 10000, point=1000)
    rec = normalize_booking(raw)
    raw_index = {"1": raw}
    assert brl.calculate_recognized_booking_revenue(rec, raw_index) == 10000


def test_no_point_or_coupon_unchanged():
    raw = _raw_booking("1", 10000)
    rec = normalize_booking(raw)
    raw_index = {"1": raw}
    assert brl.calculate_recognized_booking_revenue(rec, raw_index) == 10000


def test_banktransfer_payment_not_added_separately():
    raw = {
        "id": "1", "status": "new", "price": 10000, "roomId": "686764",
        "arrival": "2026-07-25", "departure": "2026-07-26",
        "invoiceItems": [
            {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},
            {"type": "payment", "description": "BankTransfer", "lineTotal": -10000},
        ],
    }
    rec = normalize_booking(raw)
    raw_index = {"1": raw}
    assert brl.calculate_recognized_booking_revenue(rec, raw_index) == 10000


def test_prepaid_payment_not_added_separately():
    raw = {
        "id": "1", "status": "new", "price": 10000, "roomId": "686764",
        "arrival": "2026-07-25", "departure": "2026-07-26",
        "invoiceItems": [
            {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},
            {"type": "payment", "description": "事前払い", "lineTotal": -10000},
        ],
    }
    rec = normalize_booking(raw)
    raw_index = {"1": raw}
    assert brl.calculate_recognized_booking_revenue(rec, raw_index) == 10000


def test_onsite_payment_marker_not_added_separately():
    raw = {
        "id": "1", "status": "new", "price": 10000, "roomId": "686764",
        "arrival": "2026-07-25", "departure": "2026-07-26",
        "invoiceItems": [
            {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},
            {"type": "payment", "description": "現地払い", "lineTotal": 0},
        ],
    }
    rec = normalize_booking(raw)
    raw_index = {"1": raw}
    assert brl.calculate_recognized_booking_revenue(rec, raw_index) == 10000


def test_price_zero_charge_fallback_stays_gross():
    """price=0のフォールバック済みprice(=charge合計20,000)はcouponで減らさない。"""
    raw = {
        "id": "1", "status": "confirmed", "price": 0, "roomId": "686764",
        "arrival": "2026-07-25", "departure": "2026-07-26",
        "invoiceItems": [
            {"type": "charge", "description": "", "lineTotal": 20000},
            {"type": "payment", "description": "coupon", "lineTotal": -3000},
            {"type": "payment", "description": "point", "lineTotal": -1000},
        ],
    }
    rec = normalize_booking(raw)  # price=0 fallback -> gross_revenue=20000
    assert rec.gross_revenue == 20000
    raw_index = {"1": raw}
    assert brl.calculate_recognized_booking_revenue(rec, raw_index) == 20000
    assert brl.extract_beds24_coupon_discount(raw) == 3000  # 参考額としては引き続き抽出できる


def test_missing_raw_data_still_returns_gross_revenue():
    rec = BookingRecord(booking_id="unknown", gross_revenue=12345).finalize()
    assert brl.calculate_recognized_booking_revenue(rec, {}) == 12345


# ---------------- coupon参考額(extract_beds24_coupon_discount) ----------------
def test_coupon_reference_amount_uses_abs_regardless_of_sign():
    raw_neg = _raw_booking("1", 10000, coupon=1500)  # lineTotal is -1500 in _raw_booking
    assert brl.extract_beds24_coupon_discount(raw_neg) == 1500


# ---------------- revenue_recon integration ----------------
def test_revenue_recon_uses_gross_price_and_tracks_coupon_as_reference(tmp_path):
    raw = _raw_booking("1", 31212, coupon=8800, point=1000)
    raw_path = _write_raw(tmp_path, [raw])
    rec = normalize_booking(raw)
    rec.raw_json_path = raw_path
    rec.finalize()
    result = revenue_recon.compute("2026-07", [rec], [], [], [])
    assert result["beds24_revenue_gross_stay"] == 31212
    assert result["beds24_revenue_net_for_bi"] == 31212
    assert result["beds24_revenue_basis"] == "price_gross_including_coupon_point_payments"
    assert result["beds24_revenue_logic_version"] == "beds24_revenue_v5_gross_price_coupon_reference"
    assert result["beds24_coupon_discount_amount"] == 8800
    assert result["beds24_coupon_reference_amount"] == 8800


def test_revenue_recon_cancelled_booking_excluded_regardless_of_coupon(tmp_path):
    """キャンセル予約はそもそも売上0円扱い(既存の二重減算修正を壊さない)。"""
    raw = _raw_booking("1", 31212, coupon=8800, status="cancelled")
    raw_path = _write_raw(tmp_path, [raw])
    rec = normalize_booking(raw)
    rec.raw_json_path = raw_path
    rec.finalize()
    result = revenue_recon.compute("2026-07", [rec], [], [], [])
    assert result["beds24_revenue_gross_stay"] == 0
    assert result["beds24_revenue_net_for_bi"] == 0


# ---------------- today_new_booking_details integration ----------------
def test_today_new_booking_revenue_is_gross_price(tmp_path):
    raw = _raw_booking("89646497", 31212, coupon=8800, point=1000)
    raw_path = _write_raw(tmp_path, [raw])
    rec = normalize_booking(raw)
    rec.raw_json_path = raw_path
    rec.created_at_raw = "2026-07-11T00:43:51Z"  # JST 09:43
    rec.finalize()
    from datetime import date
    result = brl.calculate_today_new_bookings_for_month(
        [rec], "2026-07", date(2026, 7, 11), EXCLUDE)
    assert result["today_new_booking_count"] == 1
    detail = result["today_new_booking_details"][0]
    assert detail["revenue_for_target_month"] == 31212
    assert result["today_new_booking_revenue"] == 31212
    assert result["today_new_booking_revenue"] == sum(
        d["revenue_for_target_month"] for d in result["today_new_booking_details"])


def test_today_new_booking_revenue_equals_details_sum_with_mixed_coupons(tmp_path):
    raws = [
        _raw_booking("1", 10000, coupon=2000, checkin="2026-07-10", checkout="2026-07-11"),
        _raw_booking("2", 20000, checkin="2026-07-12", checkout="2026-07-13"),
    ]
    raw_path = _write_raw(tmp_path, raws)
    recs = []
    for raw in raws:
        r = normalize_booking(raw)
        r.raw_json_path = raw_path
        r.created_at_raw = "2026-07-11T00:00:00Z"
        r.finalize()
        recs.append(r)
    from datetime import date
    result = brl.calculate_today_new_bookings_for_month(recs, "2026-07", date(2026, 7, 11), EXCLUDE)
    assert result["today_new_booking_count"] == 2
    assert result["today_new_booking_revenue"] == 10000 + 20000  # couponは減算しない
    assert result["today_new_booking_revenue"] == sum(
        d["revenue_for_target_month"] for d in result["today_new_booking_details"])


def test_month_crossing_booking_prorates_gross_price():
    """price=30,000(couponがあっても総額のまま)。3泊中対象月1泊 => 10,000。"""
    raw = _raw_booking("1", 30000, coupon=6000, checkin="2026-08-30", checkout="2026-09-02")
    rec = normalize_booking(raw)
    rec.created_at_raw = "2026-07-11T00:00:00Z"
    rec.finalize()
    from datetime import date
    aug = brl.calculate_today_new_bookings_for_month([rec], "2026-08", date(2026, 7, 11), EXCLUDE)
    sep = brl.calculate_today_new_bookings_for_month([rec], "2026-09", date(2026, 7, 11), EXCLUDE)
    assert aug["today_new_booking_details"][0]["revenue_for_target_month"] == 20000  # 30000*2/3
    assert sep["today_new_booking_details"][0]["revenue_for_target_month"] == 10000  # 30000*1/3


# ---------------- room_type_metrics / ADR / RevPAR integration ----------------
def test_room_type_revenue_mix_uses_gross_price(tmp_path):
    raw = _raw_booking("1", 31212, coupon=8800, point=1000, room_id="686764")
    raw_path = _write_raw(tmp_path, [raw])
    rec = normalize_booking(raw)
    rec.raw_json_path = raw_path
    rec.finalize()
    cfg = rtm.load_room_type_config()
    result = rtm.calculate_room_type_metrics([rec], "2026-07", cfg, EXCLUDE)
    assert result["adr_basis"] == "price_gross_including_coupon_point_payments"
    assert result["revpar_basis"] == "price_gross_including_coupon_point_payments"
    mix = result["room_type_revenue_mix"]
    booked_row = next(r for r in mix if r["room_type"] == "family_washitsu")
    assert booked_row["revenue"] == 31212
    assert all(r["revenue"] == 0 for r in mix if r["room_type"] != "family_washitsu")
    assert result["adr_gross"] == 31212  # 1泊なのでADR=売上そのまま


def test_snapshot_integration_gross_revenue_and_basis_fields_present(tmp_path):
    raw = _raw_booking("1", 31212, coupon=8800, point=1000, checkin="2026-07-10", checkout="2026-07-11")
    raw_path = _write_raw(tmp_path, [raw])
    conn = db.connect(tmp_path / "t.sqlite")
    rec = normalize_booking(raw)
    rec.raw_json_path = raw_path
    rec.finalize()
    db.upsert(conn, "beds24_bookings", [rec])
    from datetime import date
    ctx = monthly.assemble("2026-07", conn, today_jst=date(2026, 7, 11))
    assert ctx["revenue_recon"]["beds24_revenue_gross_stay"] == 31212
    assert ctx["revenue_recon"]["beds24_revenue_basis"] == "price_gross_including_coupon_point_payments"
    assert ctx["revenue_recon"]["beds24_revenue_logic_version"] == "beds24_revenue_v5_gross_price_coupon_reference"
    assert ctx["revenue_recon"]["beds24_coupon_reference_amount"] == 8800
    conn.close()


# ---------------- 月跨ぎ・複数raw_json_pathにまたがるケース(2026-07-11発覚のバグの回帰防止) ----------------
# 対象月をまたぐ予約が混ざると、その月の"relevant"リストは複数の別ファイル(月ごとの
# raw_json_path)にまたがる。以前は「最初に見つかった1件だけ読む」実装だったため、後から
# 見つかる方のファイルにしかデータが無い予約でinvoiceItems抽出(point/onsite)が漏れていた。
# v5でcouponは売上非控除になったためrevenue自体はこのバグの影響を受けなくなったが、
# pointの type=charge 加算判定・現地決済判定は引き続きraw_indexに依存するため、
# _load_raw_index_multi による複数ファイルマージは今も意味がある。
def test_calculate_today_new_bookings_merges_raw_index_across_multiple_files_for_point_extraction(tmp_path):
    june_raw = _raw_booking("100", 20000, checkin="2026-06-29", checkout="2026-07-02")  # 月跨ぎ
    july_raw = {
        "id": "89646497", "status": "new", "price": 10000, "roomId": "686764",
        "arrival": "2026-07-25", "departure": "2026-07-26",
        "invoiceItems": [
            {"type": "charge", "description": "[ROOMNAME1]", "lineTotal": 10000},
            # type=chargeのpoint行(施設収入として加算対象。実データでは未出現だが抽出ロジック自体の検証用)
            {"type": "charge", "description": "point bonus", "lineTotal": 500},
        ],
    }

    june_path = tmp_path / "2026-06.json"
    june_path.write_text(json.dumps([june_raw]), encoding="utf-8")
    july_path = tmp_path / "2026-07.json"
    july_path.write_text(json.dumps([july_raw]), encoding="utf-8")

    june_rec = normalize_booking(june_raw)
    june_rec.raw_json_path = str(june_path)
    june_rec.created_at_raw = "2026-07-11T00:00:00Z"
    june_rec.finalize()

    july_rec = normalize_booking(july_raw)
    july_rec.raw_json_path = str(july_path)
    july_rec.created_at_raw = "2026-07-11T00:43:51Z"
    july_rec.finalize()

    # june_recを先頭に置く(旧実装のバグは「relevantの先頭のraw_json_pathだけ読む」ことで
    # 発生していたため、意図的にjune_recを先にする)。
    from datetime import date
    result = brl.calculate_today_new_bookings_for_month(
        [june_rec, july_rec], "2026-07", date(2026, 7, 11), EXCLUDE)
    detail = next(d for d in result["today_new_booking_details"] if d["booking_id"] == "89646497")
    # revenue自体はgross price(10000+500=10500、chargeのpoint行は室料の一部として合算済み)
    # ではなくnormalize_booking()のprice(10000)ベース。type=charge point行の500円は
    # extract_beds24_point_revenue経由でonsite同様に別途加算される。
    assert detail["revenue_for_target_month"] == 10500, (
        "6月ファイルのbookingが先頭にあることでraw_indexが6月ファイルだけになり、"
        "7月のみに存在する予約のtype=charge point加算(500円)が漏れていないこと"
    )
