"""売上サマリ（喜らく単体）。

設計の前提（重要）:
  Beds24売上 = チェックイン(宿泊)月ベースの速報・宿泊KPI。
  銀行OTA入金 = 入金月ベースの資金実績。
  OTA精算は最低1か月遅れるため、同月のBeds24売上と同月の銀行入金を
  直接差分比較してはいけない（ミスリード）。
本モジュールは両者を「宿泊月速報(A)」と「入金月実績(B)」に分けて提示し、
同月比較(legacy)は参考値に格下げする。本当のreconciliationはOTA精算明細
(settlement table: settlement_month/stay_month/OTA/gross/commission/net/deposit_date)
が入った時点で実装する。
"""
from __future__ import annotations

import calendar
from typing import Dict, List

from .. import config
from ..normalize.schema import (BankTransaction, BookingRecord, CashTransaction,
                                JournalEntry)
from . import beds24_revenue_logic

# revenue_data_status 許可値
STATUS_SOKUHO = "速報"               # Beds24のみ
STATUS_DEPOSIT = "入金実績あり"        # 銀行/OTA入金あり
STATUS_PENDING = "精算明細待ち"        # 銀行入金あるが宿泊月別精算明細と未照合
STATUS_CONFIRMED = "会計確定"          # 会計士/OTA精算明細で宿泊月対応まで確定

LAG_NOTE = ("OTA入金は最低1か月遅れ。Beds24宿泊月売上(A)と銀行入金月実績(B)は"
            "対象コホートが異なるため同月差分比較しない（参考値はlegacyに格下げ）。")


def _days_in_month(month: str) -> int:
    y, m = (int(x) for x in month.split("-"))
    return calendar.monthrange(y, m)[1]


def compute(month: str,
            bookings: List[BookingRecord],
            confirmed: List[JournalEntry],
            bank_txns: List[BankTransaction],
            cash_txns: List[CashTransaction] = None) -> Dict:
    cash_txns = cash_txns or []
    rev_cfg = config.kiraku().get("revenue", {})
    exclude = rev_cfg.get("exclude_statuses", ["cancelled", "canceled", "black"])
    rooms = int(config.kiraku().get("property", {}).get("rooms", 19))

    # ---- A. 宿泊月ベース速報（Beds24）----
    # 予約単位の認識売上 = price(price=0はcharge fallback済み)そのまま(総額)。
    # coupon/point/banktransfer/事前決済/現地決済はすべてpriceの決済チャネル内訳に過ぎず、
    # 売上へ別途加算・控除しない(2026-07-11 v5、ユーザー最終判断で確定)。
    # couponの月次合計はbeds24_coupon_discount_amount/beds24_coupon_reference_amountとして
    # 参考表示のみ行う。
    in_month = [b for b in bookings if (b.checkin_date or "")[:7] == month]
    active = [b for b in in_month if not b.is_cancelled(exclude)]
    raw_json_path = next((b.raw_json_path for b in in_month if b.raw_json_path), None)
    raw_index = beds24_revenue_logic._load_raw_index(raw_json_path)

    def _recognized(b: BookingRecord) -> float:
        return beds24_revenue_logic.calculate_recognized_booking_revenue(b, raw_index)

    gross = sum(_recognized(b) for b in in_month)
    recognized = sum(_recognized(b) for b in active)
    cancelled = sum(_recognized(b) for b in in_month if b.is_cancelled(exclude))
    room_nights = sum(max(1, b.stay_nights) * max(1, b.rooms) for b in active)
    available = rooms * _days_in_month(month)
    adr = round(recognized / room_nights) if room_nights else 0
    occupancy = round(room_nights / available, 4) if available else 0.0
    revpar = round(recognized / available) if available else 0

    # ---- point加算・coupon直割引・現地決済確認（Phase 0調査で実証済みのロジック。キャンセルは二重控除しない）----
    # coupon は直割引扱いのため売上に加算しない。point・現地決済は施設収入として加算対象
    # （ただし現地決済は実データ上、原則既にpriceへ計上済みのため加算額は0のことが多い）。
    revenue_logic_info = beds24_revenue_logic.compute(month, bookings, exclude)
    point_revenue = revenue_logic_info["beds24_point_revenue_included"]
    onsite_payment_revenue = revenue_logic_info["beds24_onsite_payment_revenue_included"]
    net_for_bi = round(recognized + point_revenue + onsite_payment_revenue)

    # ---- B. 入金月ベース会計/資金実績（銀行/現金）----
    def _rev(src):
        return sum(e.credit_amount for e in confirmed
                   if e.credit_account == "宿泊売上" and e.source == src)
    bank_ota_rev = round(_rev("bank"))
    cash_rev = round(_rev("cash"))
    acct = bank_ota_rev + cash_rev                    # 入金ベース確定PL売上
    bm = [t for t in bank_txns if (t.transaction_date or "")[:7] == month]
    inflow = round(sum(t.deposit_amount for t in bm))
    outflow = round(sum(t.withdrawal_amount for t in bm))
    cm = [t for t in cash_txns if (t.transaction_date or "")[:7] == month]
    cash_in = sum(t.amount for t in cm if t.transaction_type == "現金入金")
    cash_out = sum(t.amount for t in cm if t.transaction_type == "現金支払")
    net_cash = round(inflow - outflow + cash_in - cash_out)

    # ---- revenue_data_status（同月入金額では会計確定にしない）----
    has_bank_inflow = inflow > 0
    if bank_ota_rev > 0:
        status = STATUS_PENDING        # OTA入金あり・宿泊月別精算明細と未照合
    elif has_bank_inflow:
        status = STATUS_DEPOSIT
    else:
        status = STATUS_SOKUHO

    return {
        "month": month,
        # A. 宿泊月ベース速報（Beds24）
        "beds24_stay_month_gross_revenue": round(gross),
        # 互換field: 新ロジック(point加算込み)の値と同値にする。point=0の間は従来値と一致。
        "beds24_stay_month_revenue_excluding_cancelled": net_for_bi,
        "beds24_stay_month_cancelled_revenue": round(cancelled),
        # --- 売上速報ロジック v5（総額ベース。couponはBI参考表示のみで売上非控除）---
        "beds24_revenue_gross_stay": round(recognized),  # キャンセル除外後の総額(coupon等控除前)
        "beds24_revenue_basis": beds24_revenue_logic.REVENUE_BASIS,
        "beds24_point_revenue_included": point_revenue,
        "beds24_point_booking_count": revenue_logic_info["beds24_point_booking_count"],
        "beds24_coupon_discount_detected": revenue_logic_info["beds24_coupon_discount_detected"],
        "beds24_coupon_discount_amount": revenue_logic_info["beds24_coupon_discount_amount"],
        "beds24_coupon_reference_amount": revenue_logic_info["beds24_coupon_reference_amount"],
        "beds24_coupon_discount_booking_count":
            revenue_logic_info["beds24_coupon_discount_booking_count"],
        "beds24_onsite_payment_revenue_included": onsite_payment_revenue,
        "beds24_onsite_payment_booking_count": revenue_logic_info["beds24_onsite_payment_booking_count"],
        "beds24_onsite_payment_candidate_amount":
            revenue_logic_info["beds24_onsite_payment_candidate_amount"],
        "beds24_onsite_payment_candidate_count":
            revenue_logic_info["beds24_onsite_payment_candidate_count"],
        "beds24_onsite_payment_logic_status": revenue_logic_info["beds24_onsite_payment_logic_status"],
        "beds24_onsite_payment_logic_note": revenue_logic_info["beds24_onsite_payment_logic_note"],
        # 旧field（意味が誤っていたためdeprecated。互換性のため0で残す。UIでは使わない）
        "beds24_coupon_revenue_included": revenue_logic_info["beds24_coupon_revenue_included"],
        "beds24_coupon_booking_count": revenue_logic_info["beds24_coupon_booking_count"],
        "beds24_cancelled_revenue_excluded": round(cancelled),
        "beds24_revenue_net_for_bi": net_for_bi,          # BI主指標
        "beds24_revenue_logic_version": revenue_logic_info["beds24_revenue_logic_version"],
        "beds24_revenue_logic_status": revenue_logic_info["beds24_revenue_logic_status"],
        "beds24_revenue_logic_note": revenue_logic_info["beds24_revenue_logic_note"],
        "beds24_cancelled_booking_count": revenue_logic_info["beds24_cancelled_booking_count"],
        "adr": adr,
        "revpar": revpar,
        "occupancy": occupancy,
        "room_nights": room_nights,
        "available_room_nights": available,
        "break_even_achievement_rate_sokuho": None,   # monthly側でbreakeven算出後に設定
        # B. 入金月ベース会計/資金実績（銀行/現金）
        "bank_deposit_month_ota_revenue": bank_ota_rev,
        "bank_deposit_month_total_inflow": inflow,
        "bank_deposit_month_total_outflow": outflow,
        "cash_in_basis_revenue": cash_rev,
        "net_cash_movement": net_cash,
        "accounting_revenue_confirmed": round(acct),
        # C. 精算ラグ注記
        "ota_settlement_lag_note": LAG_NOTE,
        "same_month_revenue_comparison_applicable": False,
        "revenue_comparison_status": "同月比較対象外",
        "settlement_reconciliation_status": "未実装（精算明細待ち）",
        "revenue_data_status": status,
        # legacy（同月比較・ミスリードのため参考値。判定には使わない）
        "legacy_same_month_reference": {
            "note": "同月比較はOTA精算ラグによりミスリード。参考値のみ。",
            "revenue_reconciliation_difference_same_month":
                round(recognized - acct),
            "expected_accounts_receivable_same_month":
                round(max(0.0, recognized - bank_ota_rev)),
        },
    }


def display_lines(rec: Dict) -> List[Dict]:
    """Excel/レポート用の表示項目（A 宿泊月速報 / B 入金月実績 / C 注記）。"""
    return [
        {"区分": "A.宿泊月速報(Beds24)", "項目": "宿泊月売上(キャンセル除外)",
         "値": rec["beds24_stay_month_revenue_excluding_cancelled"]},
        {"区分": "A.宿泊月速報(Beds24)", "項目": "キャンセル保持額(速報)",
         "値": rec["beds24_stay_month_cancelled_revenue"]},
        {"区分": "A.宿泊月速報(Beds24)", "項目": "ADR",
         "値": rec["adr"]},
        {"区分": "A.宿泊月速報(Beds24)", "項目": "RevPAR",
         "値": rec["revpar"]},
        {"区分": "A.宿泊月速報(Beds24)", "項目": "稼働率",
         "値": rec["occupancy"]},
        {"区分": "B.入金月実績(銀行/現金)", "項目": "OTA入金売上",
         "値": rec["bank_deposit_month_ota_revenue"]},
        {"区分": "B.入金月実績(銀行/現金)", "項目": "現金売上",
         "値": rec["cash_in_basis_revenue"]},
        {"区分": "B.入金月実績(銀行/現金)", "項目": "会計認識売上(入金ベース)",
         "値": rec["accounting_revenue_confirmed"]},
        {"区分": "B.入金月実績(銀行/現金)", "項目": "総入金",
         "値": rec["bank_deposit_month_total_inflow"]},
        {"区分": "B.入金月実績(銀行/現金)", "項目": "総出金",
         "値": rec["bank_deposit_month_total_outflow"]},
        {"区分": "B.入金月実績(銀行/現金)", "項目": "現預金純増減",
         "値": rec["net_cash_movement"]},
        {"区分": "C.精算ラグ注記", "項目": "同月比較",
         "値": rec["revenue_comparison_status"]},
        {"区分": "C.精算ラグ注記", "項目": "settlement_reconciliation_status",
         "値": rec["settlement_reconciliation_status"]},
        {"区分": "状態", "項目": "revenue_data_status",
         "値": rec["revenue_data_status"]},
    ]
