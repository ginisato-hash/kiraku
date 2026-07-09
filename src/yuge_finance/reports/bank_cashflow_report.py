"""銀行口座実績 月次キャッシュフロー集計・固定費/変動費 更新候補（喜らく単体）。

data/output/latest/bi/ に以下を出力する（すべて候補。config/fixed_variable_model.yml は直接更新しない）:
  - bank_cashflow_summary.json
  - bank_cost_model_candidates.json
  - fixed_variable_model_update_candidates.json
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..accounting import bank_classifier
from ..ingest import bank_actuals
from ..normalize.schema import BankActualTransaction

VARIABLE_KEYS = {"variable_revenue_linked", "variable_occupied_day", "variable_room_night"}
REVENUE_CATEGORIES = {"revenue_cash_in", "ota_receivable_collection", "coupon_point_receivable_collection"}


def _jst_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def build_cashflow_summary(txns: List[BankActualTransaction], month: str,
                           rules_cfg: Optional[dict] = None) -> Dict:
    rules_cfg = rules_cfg if rules_cfg is not None else bank_classifier.load_rules()
    month_txns = [t for t in txns if t.transaction_date[:7] == month]

    total_deposits = round(sum(t.deposit_amount for t in month_txns), 2)
    total_withdrawals = round(sum(t.withdrawal_amount for t in month_txns), 2)
    ending_balance_observed = None
    if month_txns:
        latest = max(month_txns, key=lambda t: (t.transaction_date, t.row_number))
        ending_balance_observed = latest.balance_after

    by_category: Dict[str, Dict] = defaultdict(lambda: {"count": 0, "amount": 0.0})
    by_counterparty: Dict[str, Dict] = defaultdict(lambda: {"count": 0, "amount": 0.0})
    fixed_candidates, variable_candidates, debt_candidates = [], [], []
    revenue_candidates, review_needed = [], []

    for t in month_txns:
        cls = bank_classifier.classify_bank_transaction(t, rules_cfg)
        amt = abs(t.signed_amount)
        cat_bucket = by_category[cls["cost_model_category"]]
        cat_bucket["count"] += 1
        cat_bucket["amount"] = round(cat_bucket["amount"] + amt, 2)
        cp = t.counterparty_normalized or t.counterparty_raw or "(摘要なし)"
        cp_bucket = by_counterparty[cp]
        cp_bucket["count"] += 1
        cp_bucket["amount"] = round(cp_bucket["amount"] + amt, 2)

        item = {
            "transaction_date": t.transaction_date, "counterparty": cp, "amount": amt,
            "cost_model_category": cls["cost_model_category"],
            "fixed_or_variable": cls["fixed_or_variable"], "confidence": cls["confidence"],
            "requires_review": cls["requires_review"], "reason": cls["reason"],
        }
        if cls["fixed_or_variable"] == "fixed":
            fixed_candidates.append(item)
        elif cls["fixed_or_variable"] in VARIABLE_KEYS:
            variable_candidates.append(item)
        if cls["fixed_or_variable"] == "debt_service":
            debt_candidates.append(item)
        if cls["cost_model_category"] in REVENUE_CATEGORIES:
            revenue_candidates.append(item)
        if cls["requires_review"] or cls["cost_model_category"] == "unknown":
            review_needed.append(item)

    return {
        "month": month,
        "total_deposits": total_deposits,
        "total_withdrawals": total_withdrawals,
        "net_cashflow": round(total_deposits - total_withdrawals, 2),
        "ending_balance_observed": ending_balance_observed,
        "by_cost_model_category": dict(by_category),
        "by_counterparty": dict(by_counterparty),
        "fixed_cost_candidates": fixed_candidates,
        "variable_cost_candidates": variable_candidates,
        "debt_service_candidates": debt_candidates,
        "revenue_collection_candidates": revenue_candidates,
        "unknown_or_review_required": review_needed,
    }


def build_config_update_candidates(txns: List[BankActualTransaction],
                                   rules_cfg: Optional[dict] = None) -> Dict:
    """取引先×費目カテゴリ単位に集約した config反映候補（候補のみ。自動反映しない）。"""
    rules_cfg = rules_cfg if rules_cfg is not None else bank_classifier.load_rules()
    by_vendor: Dict[tuple, Dict] = defaultdict(lambda: {"amounts": [], "months": set(), "cls": None})

    for t in txns:
        if t.signed_amount >= 0:
            continue  # 出金(費用)のみを費目候補の対象にする
        cls = bank_classifier.classify_bank_transaction(t, rules_cfg)
        vendor = t.counterparty_normalized or t.counterparty_raw or "(摘要なし)"
        rec = by_vendor[(cls["cost_model_category"], vendor)]
        rec["amounts"].append(abs(t.signed_amount))
        rec["months"].add(t.transaction_date[:7])
        rec["cls"] = cls

    fixed_candidates, variable_candidates, debt_candidates, review = [], [], [], []
    for (category, vendor), rec in by_vendor.items():
        cls = rec["cls"]
        n_months = max(len(rec["months"]), 1)
        entry = {
            "cost_model_category": category, "counterparty": vendor,
            "months_observed": sorted(rec["months"]),
            "avg_monthly_amount": round(sum(rec["amounts"]) / n_months, 2),
            "total_amount": round(sum(rec["amounts"]), 2),
            "confidence": cls["confidence"], "auto_reflectable": cls["auto_reflectable"],
            "requires_review": cls["requires_review"], "reason": cls["reason"],
            "evidence_source": cls["evidence_source"],
        }
        if cls["fixed_or_variable"] == "fixed":
            fixed_candidates.append(entry)
        elif cls["fixed_or_variable"] in VARIABLE_KEYS:
            variable_candidates.append({
                **entry,
                "note": "Beds24宿泊月売上との同月比較は行わないため、料率(%)は算出せず月次平均額のみを候補として記録する。",
            })
        if cls["fixed_or_variable"] == "debt_service":
            debt_candidates.append(entry)
        if cls["requires_review"] or category == "unknown":
            review.append(entry)

    return {
        "generated_at_jst": _jst_now(),
        "source": "bank_actual_transactions",
        "fixed_cost_candidates": fixed_candidates,
        "variable_cost_rate_candidates": variable_candidates,
        "debt_service_candidates": debt_candidates,
        "requires_review": review,
    }


def write_all(conn, bi_dir: Path, month: Optional[str] = None) -> Dict:
    """data/output/latest/bi/ へ3ファイルを出力する。config/fixed_variable_model.yml は変更しない。"""
    bi_dir.mkdir(parents=True, exist_ok=True)
    txns = bank_actuals.load_all(conn)
    rules_cfg = bank_classifier.load_rules()
    months = sorted({t.transaction_date[:7] for t in txns if t.transaction_date})
    target_month = month if month in months else (months[-1] if months else None)

    summaries = {m: build_cashflow_summary(txns, m, rules_cfg) for m in months}
    latest_summary = summaries.get(target_month, {})

    cashflow_out = {
        "generated_at_jst": _jst_now(),
        **{k: v for k, v in latest_summary.items()
           if k not in ("fixed_cost_candidates", "variable_cost_candidates", "debt_service_candidates",
                        "revenue_collection_candidates", "unknown_or_review_required")},
        "months": summaries,
    }
    cost_model_out = {
        "generated_at_jst": _jst_now(),
        "month": target_month,
        "fixed_cost_candidates": latest_summary.get("fixed_cost_candidates", []),
        "variable_cost_candidates": latest_summary.get("variable_cost_candidates", []),
        "debt_service_candidates": latest_summary.get("debt_service_candidates", []),
        "revenue_collection_candidates": latest_summary.get("revenue_collection_candidates", []),
        "unknown_or_review_required": latest_summary.get("unknown_or_review_required", []),
        "by_month": {m: {k: s[k] for k in
                         ("fixed_cost_candidates", "variable_cost_candidates", "debt_service_candidates",
                          "revenue_collection_candidates", "unknown_or_review_required")}
                    for m, s in summaries.items()},
    }
    config_candidates_out = build_config_update_candidates(txns, rules_cfg)

    (bi_dir / "bank_cashflow_summary.json").write_text(
        json.dumps(cashflow_out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (bi_dir / "bank_cost_model_candidates.json").write_text(
        json.dumps(cost_model_out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (bi_dir / "fixed_variable_model_update_candidates.json").write_text(
        json.dumps(config_candidates_out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    return {
        "months": months, "target_month": target_month,
        "bank_cashflow_summary": str(bi_dir / "bank_cashflow_summary.json"),
        "bank_cost_model_candidates": str(bi_dir / "bank_cost_model_candidates.json"),
        "fixed_variable_model_update_candidates":
            str(bi_dir / "fixed_variable_model_update_candidates.json"),
    }


def compute_bi_fields(conn) -> Dict:
    """bi_snapshot.json に載せる bank_* フィールドをまとめて返す。"""
    txns = bank_actuals.load_all(conn)
    empty = {
        "bank_actual_latest_balance": None, "bank_actual_latest_balance_date": None,
        "bank_source_period_start": None, "bank_source_period_end": None,
        "bank_total_deposits": None, "bank_total_withdrawals": None, "bank_net_cashflow": None,
        "bank_month_end_balance_observed": None, "bank_month_end_balance_date": None,
        "bank_opening_balance_before_first_transaction": None,
        "bank_balance_reconciliation_status": "未取込",
        "accountant_bs_cash_balance": None, "bank_csv_observed_balance": None,
        "bank_csv_observed_date": None, "bank_vs_accountant_difference": None,
        "bank_csv_import_status": "未取込", "bank_csv_imported_rows": 0,
        "bank_csv_source_file_name": None,
        "bank_classification_review_required_count": 0,
        "bank_fixed_cost_candidate_total": 0, "bank_variable_cost_candidate_total": 0,
        "bank_debt_service_candidate_total": 0,
        # ローカルに銀行実績データが無い状態。publish-bi-r2 --preserve-bank-fields-from-r2 が
        # 直近公開snapshotに有効な銀行データを見つけた場合のみ "previous_r2_snapshot" へ上書きされる。
        "bank_fields_source": "not_available",
    }
    if not txns:
        return empty

    dates = sorted(t.transaction_date for t in txns if t.transaction_date)
    latest = max(txns, key=lambda t: (t.transaction_date, t.row_number))
    chain = bank_actuals.verify_balance_chain(txns)
    recon = bank_actuals.reconcile_with_accountant_bs(conn, txns)
    last_month = dates[-1][:7] if dates else None
    month_end = bank_actuals.month_end_observed_balance(txns, last_month) if last_month else None
    total_deposits = round(sum(t.deposit_amount for t in txns), 2)
    total_withdrawals = round(sum(t.withdrawal_amount for t in txns), 2)

    rules_cfg = bank_classifier.load_rules()
    classified = [bank_classifier.classify_bank_transaction(t, rules_cfg) for t in txns]
    review_count = sum(1 for c in classified if c["requires_review"])
    fixed_total = round(sum(abs(t.signed_amount) for t, c in zip(txns, classified)
                            if c["fixed_or_variable"] == "fixed" and t.signed_amount < 0), 2)
    variable_total = round(sum(abs(t.signed_amount) for t, c in zip(txns, classified)
                               if c["fixed_or_variable"] in VARIABLE_KEYS and t.signed_amount < 0), 2)
    debt_total = round(sum(abs(t.signed_amount) for t, c in zip(txns, classified)
                           if c["fixed_or_variable"] == "debt_service" and t.signed_amount < 0), 2)

    return {
        "bank_actual_latest_balance": latest.balance_after,
        "bank_actual_latest_balance_date": latest.transaction_date,
        "bank_source_period_start": dates[0] if dates else None,
        "bank_source_period_end": dates[-1] if dates else None,
        "bank_total_deposits": total_deposits, "bank_total_withdrawals": total_withdrawals,
        "bank_net_cashflow": round(total_deposits - total_withdrawals, 2),
        "bank_month_end_balance_observed": month_end["balance"] if month_end else None,
        "bank_month_end_balance_date": month_end["date"] if month_end else None,
        "bank_opening_balance_before_first_transaction":
            chain["opening_balance_before_first_transaction"],
        "bank_balance_reconciliation_status": recon["bank_balance_reconciliation_status"],
        "accountant_bs_cash_balance": recon["accountant_bs_cash_balance"],
        "bank_csv_observed_balance": recon["bank_csv_observed_balance"],
        "bank_csv_observed_date": recon["bank_csv_observed_date"],
        "bank_vs_accountant_difference": recon["bank_vs_accountant_difference"],
        "bank_csv_import_status": "imported",
        "bank_csv_imported_rows": len(txns),
        "bank_csv_source_file_name": latest.source_file_name,
        "bank_classification_review_required_count": review_count,
        "bank_fixed_cost_candidate_total": fixed_total,
        "bank_variable_cost_candidate_total": variable_total,
        "bank_debt_service_candidate_total": debt_total,
        "bank_fields_source": "current_import",
    }
