"""予約別の認識売上額 = price(price=0はcharge fallback済み) - クーポン利用額。

2026-07-11、予約89646497の実データ検証でユーザーに確認済みの結論:
  - invoiceItems type=charge(室料)とtype=payment(point/coupon/事前払い等)の合計は
    常に相殺する(point/coupon signalのある181予約中178件で完全一致)。
  - クーポンはOTA等からの補填が無く施設が実質負担するため、その分だけ実収入が減る
    → 売上から控除する。
  - ポイントはOTA/ポイント発行元から施設へ別途入金される決済チャネルの一つに過ぎず、
    price(=charge合計)に既に含まれるため加算しない(従来通り)。
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
def test_booking_89646497_real_case_coupon_deducted_point_not_added(tmp_path):
    """実データ: price=31,212 / point=1,000 / coupon=8,800 => recognized=22,412。"""
    raw = _raw_booking("89646497", 31212, coupon=8800, point=1000)
    raw_path = _write_raw(tmp_path, [raw])
    rec = normalize_booking(raw)
    rec.raw_json_path = raw_path
    rec.finalize()
    raw_index = brl._load_raw_index(raw_path)
    assert brl.calculate_recognized_booking_revenue(rec, raw_index) == 22412


def test_coupon_only_deducted():
    raw = _raw_booking("1", 10000, coupon=2000)
    rec = normalize_booking(raw)
    raw_index = {"1": raw}
    assert brl.calculate_recognized_booking_revenue(rec, raw_index) == 8000


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


def test_price_zero_charge_fallback_then_coupon_deducted():
    """price=0のフォールバック済みprice(=charge合計20,000)からcouponを控除する。"""
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
    assert brl.calculate_recognized_booking_revenue(rec, raw_index) == 17000


def test_negative_line_total_coupon_handled_via_abs():
    raw = _raw_booking("1", 10000, coupon=1500)
    # lineTotal is already negative (-1500) in _raw_booking; verify abs handling holds
    rec = normalize_booking(raw)
    raw_index = {"1": raw}
    assert brl.calculate_recognized_booking_revenue(rec, raw_index) == 8500


def test_recognized_revenue_never_negative():
    raw = _raw_booking("1", 5000, coupon=9000)
    rec = normalize_booking(raw)
    raw_index = {"1": raw}
    assert brl.calculate_recognized_booking_revenue(rec, raw_index) == 0


def test_missing_raw_data_defaults_coupon_to_zero():
    """raw_indexに対応するデータが無ければcoupon=0扱い(price/gross_revenueをそのまま使う)。"""
    rec = BookingRecord(booking_id="unknown", gross_revenue=12345).finalize()
    assert brl.calculate_recognized_booking_revenue(rec, {}) == 12345


# ---------------- revenue_recon integration ----------------
def test_revenue_recon_deducts_coupon_from_recognized_revenue(tmp_path):
    raw = _raw_booking("1", 31212, coupon=8800, point=1000)
    raw_path = _write_raw(tmp_path, [raw])
    rec = normalize_booking(raw)
    rec.raw_json_path = raw_path
    rec.finalize()
    result = revenue_recon.compute("2026-07", [rec], [], [], [])
    assert result["beds24_revenue_gross_stay"] == 22412
    assert result["beds24_revenue_net_for_bi"] == 22412
    assert result["beds24_revenue_basis"] == "price_minus_coupon"
    assert result["beds24_coupon_discount_amount"] == 8800


def test_revenue_recon_cancelled_booking_excluded_regardless_of_coupon(tmp_path):
    """キャンセル予約はクーポン控除以前にそもそも売上0円扱い(既存の二重減算修正を壊さない)。"""
    raw = _raw_booking("1", 31212, coupon=8800, status="cancelled")
    raw_path = _write_raw(tmp_path, [raw])
    rec = normalize_booking(raw)
    rec.raw_json_path = raw_path
    rec.finalize()
    result = revenue_recon.compute("2026-07", [rec], [], [], [])
    assert result["beds24_revenue_gross_stay"] == 0
    assert result["beds24_revenue_net_for_bi"] == 0


# ---------------- today_new_booking_details integration ----------------
def test_today_new_booking_revenue_reflects_coupon_deduction(tmp_path):
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
    assert detail["revenue_for_target_month"] == 22412
    assert result["today_new_booking_revenue"] == 22412
    assert result["today_new_booking_revenue"] == sum(
        d["revenue_for_target_month"] for d in result["today_new_booking_details"])


def test_today_new_booking_revenue_equals_details_sum_with_mixed_coupons(tmp_path):
    raws = [
        _raw_booking("1", 10000, coupon=2000, checkin="2026-07-10", checkout="2026-07-11"),
        _raw_booking("2", 20000, checkin="2026-07-12", checkout="2026-07-13"),
    ]
    raw_path = _write_raw(tmp_path, raws)
    recs = []
    for i, raw in enumerate(raws, start=1):
        r = normalize_booking(raw)
        r.raw_json_path = raw_path
        r.created_at_raw = "2026-07-11T00:00:00Z"
        r.finalize()
        recs.append(r)
    from datetime import date
    result = brl.calculate_today_new_bookings_for_month(recs, "2026-07", date(2026, 7, 11), EXCLUDE)
    assert result["today_new_booking_count"] == 2
    assert result["today_new_booking_revenue"] == 8000 + 20000
    assert result["today_new_booking_revenue"] == sum(
        d["revenue_for_target_month"] for d in result["today_new_booking_details"])


def test_month_crossing_booking_prorates_coupon_adjusted_revenue(tmp_path):
    """price=30,000・coupon=6,000 => recognized=24,000。3泊中対象月1泊 => 8,000。"""
    raw = _raw_booking("1", 30000, coupon=6000, checkin="2026-08-30", checkout="2026-09-02")
    raw_path = _write_raw(tmp_path, [raw])
    rec = normalize_booking(raw)
    rec.raw_json_path = raw_path
    rec.created_at_raw = "2026-07-11T00:00:00Z"
    rec.finalize()
    from datetime import date
    aug = brl.calculate_today_new_bookings_for_month([rec], "2026-08", date(2026, 7, 11), EXCLUDE)
    sep = brl.calculate_today_new_bookings_for_month([rec], "2026-09", date(2026, 7, 11), EXCLUDE)
    assert aug["today_new_booking_details"][0]["revenue_for_target_month"] == 16000  # 24000*2/3
    assert sep["today_new_booking_details"][0]["revenue_for_target_month"] == 8000   # 24000*1/3


# ---------------- room_type_metrics / ADR / RevPAR integration ----------------
def test_room_type_revenue_mix_reflects_coupon_adjusted_revenue(tmp_path):
    raw = _raw_booking("1", 31212, coupon=8800, point=1000, room_id="686764")
    raw_path = _write_raw(tmp_path, [raw])
    rec = normalize_booking(raw)
    rec.raw_json_path = raw_path
    rec.finalize()
    cfg = rtm.load_room_type_config()
    result = rtm.calculate_room_type_metrics([rec], "2026-07", cfg, EXCLUDE)
    assert result["adr_basis"] == "price_minus_coupon"
    assert result["revpar_basis"] == "price_minus_coupon"
    mix = result["room_type_revenue_mix"]
    booked_row = next(r for r in mix if r["room_type"] == "family_washitsu")
    assert booked_row["revenue"] == 22412
    assert all(r["revenue"] == 0 for r in mix if r["room_type"] != "family_washitsu")
    assert result["adr_gross"] == 22412  # 1泊なのでADR=売上そのまま


def test_snapshot_integration_coupon_and_revenue_basis_fields_present(tmp_path):
    raw = _raw_booking("1", 31212, coupon=8800, point=1000, checkin="2026-07-10", checkout="2026-07-11")
    raw_path = _write_raw(tmp_path, [raw])
    conn = db.connect(tmp_path / "t.sqlite")
    rec = normalize_booking(raw)
    rec.raw_json_path = raw_path
    rec.finalize()
    db.upsert(conn, "beds24_bookings", [rec])
    from datetime import date
    ctx = monthly.assemble("2026-07", conn, today_jst=date(2026, 7, 11))
    assert ctx["revenue_recon"]["beds24_revenue_gross_stay"] == 22412
    assert ctx["revenue_recon"]["beds24_revenue_basis"] == "price_minus_coupon"
    assert ctx["revenue_recon"]["beds24_revenue_logic_version"] == "beds24_revenue_v4_coupon_deducted"
    conn.close()


# ---------------- 月跨ぎ・複数raw_json_pathにまたがるケース(実データで発覚したバグの回帰防止) ----------------
# 2026-07-11発覚: 対象月をまたぐ予約が混ざると、その月の"relevant"リストは
# 複数の別ファイル(月ごとのraw_json_path)にまたがる。従来「最初に見つかった1件だけ読む」
# 実装だったため、後から見つかる方のファイルにしかデータが無い予約(実例: booking_id
# 89646497、対象月2026-07のrelevantリストに6月チェックインの月跨ぎ予約が先頭に来た結果、
# 7月のraw fileが読まれずcoupon控除が丸ごと漏れていた)。_load_raw_index_multiで
# 全ファイルをマージするよう修正済み。単一raw_json_pathのテストだけでは再現しないため、
# 意図的に2つの別ファイルを用意して検証する。
def test_calculate_today_new_bookings_merges_raw_index_across_multiple_files(tmp_path):
    june_raw = _raw_booking("100", 20000, checkin="2026-06-29", checkout="2026-07-02")  # 月跨ぎ、coupon無し
    july_raw = _raw_booking("89646497", 31212, coupon=8800, point=1000,
                            checkin="2026-07-25", checkout="2026-07-26")

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
    assert detail["revenue_for_target_month"] == 22412, (
        "6月ファイルのbookingが先頭にあることでraw_indexが6月ファイルだけになり、"
        "7月のみに存在する予約のcoupon控除が漏れていないこと"
    )


def test_room_type_metrics_merges_raw_index_across_multiple_files(tmp_path):
    # june_recとjuly_recは別の部屋タイプにして、按分後の売上が同じroom_type_revenue_mix行に
    # 混ざらないようにする(この関数の関心はraw_indexのマージであり、按分ロジックではない)。
    june_raw = _raw_booking("100", 20000, room_id="685761", checkin="2026-06-29", checkout="2026-07-02")
    july_raw = _raw_booking("89646497", 31212, coupon=8800, room_id="686764",
                            checkin="2026-07-25", checkout="2026-07-26")

    june_path = tmp_path / "2026-06.json"
    june_path.write_text(json.dumps([june_raw]), encoding="utf-8")
    july_path = tmp_path / "2026-07.json"
    july_path.write_text(json.dumps([july_raw]), encoding="utf-8")

    june_rec = normalize_booking(june_raw)
    june_rec.raw_json_path = str(june_path)
    june_rec.finalize()
    july_rec = normalize_booking(july_raw)
    july_rec.raw_json_path = str(july_path)
    july_rec.finalize()

    cfg = rtm.load_room_type_config()
    result = rtm.calculate_room_type_metrics([june_rec, july_rec], "2026-07", cfg, EXCLUDE)
    booked_row = next(r for r in result["room_type_revenue_mix"] if r["room_type"] == "family_washitsu")
    assert booked_row["revenue"] == 22412
