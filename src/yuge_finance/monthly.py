"""月次コンテキスト組み立て（喜らく単体）。

DBの取込済みデータから 仕訳→試算表→3表→ロールフォワード→KPI→検証 を再構築する。
開始残高(2026-05-31)を起点に、ロールフォワード開始月以降を累積してBSを算出する。
build-ledger / export-excel / close-month / refresh-beds24-bi から共通利用する。

労務(labor_forecast)・損益分岐(breakeven_model)・債務(debt)の計算は、いずれも
DBの既存データを読むだけの純計算（仕訳生成・PL/BS/CF確定・Excel更新を伴わない）。
"""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Dict, List

from . import config, db
from .accounting import (beds24_revenue_logic, breakeven_model, debt_journal, journal_engine,
                         kpi, labor_model, pace_model, pl_bs_cf, reconciliation,
                         revenue_recon, room_type_metrics, trial_balance)
from .ingest import opening_balance
from .normalize import validators
from .normalize.schema import JournalEntry
from .reports import bank_cashflow_report


def _prior_period_entries(conn, month: str) -> List[JournalEntry]:
    """ロールフォワード開始月〜対象月の前月 までの確定仕訳をDBから取得。"""
    start = str(config.kiraku().get("period", {}).get("rollforward_start", "2026-06"))
    names = {f.name for f in fields(JournalEntry)}
    rows = db.fetch(conn, "journal_entries",
                    'month >= ? AND month < ?', (start, month))
    return [JournalEntry(**{k: v for k, v in r.items() if k in names}) for r in rows]


def _net(entries: List[JournalEntry], account: str, normal: str) -> float:
    d = sum(e.debit_amount for e in entries if e.debit_account == account)
    c = sum(e.credit_amount for e in entries if e.credit_account == account)
    return (d - c) if normal == "debit" else (c - d)


def assemble(month: str, conn, workbook_path: Path = None, today_jst=None) -> Dict:
    """today_jst: 「本日」の基準日を明示指定する場合のみ渡す（日付跨ぎ検証用）。
    未指定時は実行時のJST今日を都度計算する（インポート時・デフォルト引数での固定は禁止）。
    """
    bookings = db.load_objects(conn, "beds24_bookings", month, "checkin_date")
    bank = db.load_objects(conn, "bank_transactions", month, "transaction_date")
    cash = db.load_objects(conn, "cash_transactions", month, "transaction_date")
    manual = db.load_objects(conn, "manual_adjustments", month, "journal_date")
    opening_records = db.load_objects(conn, "opening_balances")
    schedule = db.load_objects(conn, "loan_schedule", month, "payment_date")
    for r in bookings:
        r.finalize()
    for r in bank:
        r.finalize()

    je = journal_engine.build(month, bookings, bank, cash, manual)
    confirmed = je["confirmed"]
    exceptions = je["exceptions"]

    # --- 当月 試算表 / PL / CF（期間ベース）---
    tb = trial_balance.build(confirmed)
    tb_tot = trial_balance.totals(tb)
    pl = pl_bs_cf.build_pl(tb)
    cf = pl_bs_cf.build_cf(tb, pl["net_income"])

    # --- 累積 試算表 / BS（開始残高 + ロールフォワード）---
    opening = opening_balance.opening_dict(conn)
    opening_critical = opening_balance.critical_checks(opening_records) if opening_records else []
    prior = _prior_period_entries(conn, month)
    cum_entries = prior + confirmed
    tb_cum = trial_balance.build(cum_entries, opening=opening)
    pl_cum = pl_bs_cf.build_pl(tb_cum)
    bs = pl_bs_cf.build_bs(tb_cum, pl_cum["net_income"])

    # --- ロールフォワード（当月期首 = 開始残高 + 前月までの累積変動）---
    cash_open = (opening.get("現預金", {}).get("debit", 0)
                 - opening.get("現預金", {}).get("credit", 0)
                 + _net(prior, "現預金", "debit"))
    loan_open = (opening.get("借入金", {}).get("credit", 0)
                 - opening.get("借入金", {}).get("debit", 0)
                 + _net(prior, "借入金", "credit"))
    cash_rf = reconciliation.cash_rollforward(month, cash, cash_open)
    loan_rf = reconciliation.loan_rollforward(month, confirmed, loan_open)

    # --- 売上サマリ（宿泊月速報A / 入金月実績B / 精算ラグ注記C。同月差分比較はしない）---
    rev_recon = revenue_recon.compute(month, bookings, confirmed, bank, cash)
    beds24_rev = rev_recon["beds24_stay_month_revenue_excluding_cancelled"]

    # --- 損益分岐KPI（旧・速報。Beds24宿泊月売上ベース）---
    breakeven = kpi.build(month, confirmed, beds24_rev, bookings)
    rev_recon["break_even_achievement_rate_sokuho"] = breakeven.get("損益分岐達成率")

    # --- Phase C: 人件費予測モデル（Beds24日別稼働ベース。給与確定仕訳ではない）---
    labor = labor_model.build(month, bookings, beds24_revenue=beds24_rev)

    # --- Phase B: 月次債務返済（返済予定表と銀行明細が一致した場合のみ確定）---
    debt = debt_journal.build(month, schedule, bank)
    debt_opening_total = debt_journal.debt_balance_from_records(opening_records)
    debt_closing_total = round(debt_opening_total - debt["monthly_debt_principal_payment"], 2)

    # --- Phase D v2: 固定費・変動費モデルによる損益分岐点（現体制運営前提）---
    # cash/accounting operating BEP + finance-inclusive BEP（支払利息・元本返済込み）+ MC成功報酬。
    breakeven_new = breakeven_model.build(
        month, beds24_revenue=beds24_rev, adr=rev_recon.get("adr", 0),
        labor_total_base_case=labor["labor_total_base_case"],
        room_nights=labor.get("labor_room_nights"),
        monthly_debt_principal_payment=debt["monthly_debt_principal_payment"],
        monthly_debt_interest_payment=debt["monthly_debt_interest_payment"],
        debt_service_status=debt["debt_service_status"])

    # --- 予約ペース判定（達成率とは別軸。月内進捗率に対するBeds24 OTB売上のペース）---
    pace = pace_model.build(
        month, cash_operating_breakeven_revenue=breakeven_new["cash_operating_breakeven_revenue"],
        beds24_month_on_the_books_revenue=beds24_rev,
        adr=rev_recon.get("adr", 0))

    # --- 会計士YTD（参照）+ システムYTD（開始月〜当月）---
    pl_ytd_acct = opening_balance.accountant_pl_ytd()
    cf_ytd_acct = opening_balance.accountant_cf_ytd()
    kpi_summary_acct = opening_balance.accountant_kpi_summary()

    ok_j, jd, jc = validators.journal_balanced(confirmed)

    # --- 銀行口座実績レイヤー（BI/分析専用。仕訳・PL/BS/CFには一切反映しない）---
    bank_actual_bi = bank_cashflow_report.compute_bi_fields(conn)

    # --- 本日の新規予約（BI専用。月またぎ予約を按分するため月フィルタ無しで全件読む）---
    all_bookings = db.load_objects(conn, "beds24_bookings")
    for r in all_bookings:
        r.finalize()
    revenue_exclude = config.kiraku().get("revenue", {}).get(
        "exclude_statuses", ["cancelled", "canceled", "black"])
    effective_today_jst = today_jst if today_jst is not None else beds24_revenue_logic.jst_today()
    today_new_bookings = beds24_revenue_logic.calculate_today_new_bookings_for_month(
        all_bookings, month, effective_today_jst, revenue_exclude)
    today_new_bookings["today_jst"] = effective_today_jst.isoformat()

    # --- 部屋タイプ別KPI（ADR/日別稼働率/売上構成。月またぎ按分のため月フィルタ無し全件を使う）---
    room_type_config = room_type_metrics.load_room_type_config()
    room_type_kpi = room_type_metrics.calculate_room_type_metrics(
        all_bookings, month, room_type_config, revenue_exclude)

    # --- 本日の新規予約detailsに部屋タイプを付与（既存のroom_type_metrics分類ロジックを再利用。
    #     Beds24 payloadには予約時/現在で別々の部屋IDが無いため、両方とも現在のroom_idで揃える）---
    bookings_by_id = {b.booking_id: b for b in all_bookings}
    for detail in today_new_bookings.get("today_new_booking_details", []):
        booking = bookings_by_id.get(detail["booking_id"])
        room_type_key = (room_type_metrics.classify_room_type(booking, room_type_config)
                         if booking else "unknown")
        room_type_label = room_type_config.get(room_type_key, {}).get("label", room_type_key)
        detail["room_type"] = room_type_label
        detail["room_type_key"] = room_type_key
        detail["original_room_type"] = None
        detail["original_room_type_key"] = None
        detail["current_room_type"] = room_type_label
        detail["current_room_type_key"] = room_type_key
        detail["current_room_id"] = detail["room_id"]

    return {
        "month": month,
        "bookings": bookings, "bank_txns": bank, "cash_txns": cash, "manual": manual,
        "journal": je, "confirmed": confirmed, "exceptions": exceptions,
        "tb": tb, "tb_totals": tb_tot, "tb_cumulative": tb_cum,
        "pl": pl, "pl_cumulative": pl_cum, "bs": bs, "cf": cf,
        "breakeven": breakeven,
        "revenue_recon": rev_recon,
        "opening": opening, "opening_records": opening_records,
        "opening_critical": opening_critical,
        "accountant_pl_ytd": pl_ytd_acct, "accountant_cf_ytd": cf_ytd_acct,
        "accountant_kpi_summary": kpi_summary_acct,
        "cash_rollforward": cash_rf, "loan_rollforward": loan_rf,
        "labor_forecast": labor,
        "breakeven_model": breakeven_new,
        "pace_model": pace,
        "debt": {
            "debt_opening_balance_total": debt_opening_total,
            "debt_closing_balance_total": debt_closing_total,
            "monthly_debt_principal_payment": debt["monthly_debt_principal_payment"],
            "monthly_debt_interest_payment": debt["monthly_debt_interest_payment"],
            "monthly_debt_total_payment": debt["monthly_debt_total_payment"],
            "debt_service_status": debt["debt_service_status"],
            "debt_schedule_missing_count": debt["debt_schedule_missing_count"],
            "debt_schedule_exception_amount": debt["debt_schedule_exception_amount"],
        },
        "debt_detail": debt,
        "debit_total": jd, "credit_total": jc,
        "image_issues": 0, "workbook_path": workbook_path,
        "bank_actual_bi": bank_actual_bi,
        "today_new_bookings": today_new_bookings,
        "room_type_metrics": room_type_kpi,
    }
