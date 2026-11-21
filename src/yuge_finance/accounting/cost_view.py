"""費用の実績と予測（喜らく単体）。Issue #72 D2。

実績（actual）と予測（forecast）を同じ表に混ぜず、行ごとに種類・出所・根拠を持つ。

実績：
- 銀行口座実績（bank_actual_transactions）の出金を bank_classifier の費目候補で月別・費目別に集計する。
  借入返済・入金・回収は費用に入れない。費目不明・要確認は「未分類」として別に持ち、合計に入れない。
- 人件費は MF給与の確定実績（labor_actual_from_payroll、verified）だけを使う。給与・社会保険・労働保険の
  銀行出金は、その給与実績に含まれる支払いなので費用に加えない（重複排除）。どの月にも給与実績が無い
  場合に限り、銀行の人件費系出金を「銀行出金ベース」として載せる。給与実績の無い月の人件費は未取得（None）
  で、0 にしない。
- 請求書（OCR台帳）は支払われた時点で銀行出金として計上される。未払い請求書をここに足すと二重計上に
  なるため加えない。

予測：
- 基準モデルは「直近の完了月（銀行明細が月末まで揃った月）最大3か月の費目別平均」。人件費は同じ月数の
  給与実績平均。根拠月を必ず付ける。完了月が無ければ予測しない。
"""
from __future__ import annotations

from collections import defaultdict
from calendar import monthrange
from typing import Callable, Dict, Iterable, List, Optional

NOT_COST = {"revenue_cash_in", "loan_or_capital_cash_in", "owner_related_cash_in", "ota_receivable_collection",
            "coupon_point_receivable_collection", "debt_service_bank", "debt_service_related_party"}
PAYROLL_PAID = {"payroll_or_staff_payment", "social_insurance", "labor_insurance"}
BASELINE_MONTHS = 3


def _month_end(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{month}-{monthrange(y, m)[1]:02d}"


def actual_costs(txns: Iterable, classify: Callable[[object], dict],
                 payroll: Dict[str, Optional[Dict]]) -> Dict[str, Dict]:
    """月 → {categories, labor, unclassified, excluded_payroll_paid, complete, total}。

    payroll は 月 → labor_actual_from_payroll の結果（無い月は None）。
    """
    txns = list(txns)
    payroll_known = any(p and p.get("cost_status") == "computed" for p in payroll.values())
    last_date = max((t.transaction_date for t in txns if t.transaction_date), default="")
    months: Dict[str, Dict] = defaultdict(lambda: {"categories": defaultdict(float), "unclassified": 0.0,
                                                   "unclassified_count": 0, "excluded_payroll_paid": 0.0})
    for t in txns:
        if not t.transaction_date or t.signed_amount >= 0:
            continue
        c = classify(t)
        cat, amt = c["cost_model_category"], abs(t.signed_amount)
        m = months[t.transaction_date[:7]]
        if cat in NOT_COST:
            continue
        if cat == "unknown" or c.get("requires_review"):
            m["unclassified"] += amt; m["unclassified_count"] += 1
            continue
        if cat in PAYROLL_PAID and payroll_known:
            m["excluded_payroll_paid"] += amt
            continue
        m["categories"][cat] += amt
    out: Dict[str, Dict] = {}
    for month in sorted(set(months) | {k for k, v in payroll.items() if v}):
        m = months[month]
        p = payroll.get(month)
        cats = {k: round(v) for k, v in sorted(m["categories"].items())}
        if p and p.get("cost_status") == "computed":
            labor = {"amount": round(p["cost"]), "source": p.get("cost_source"), "basis": "mf_payroll_actual"}
        elif not payroll_known and any(k in cats for k in PAYROLL_PAID):
            # No payroll actual anywhere: the bank's payroll-type payments stand in, labelled as cash basis.
            labor = {"amount": sum(cats.pop(k) for k in sorted(PAYROLL_PAID) if k in cats), "source": "銀行出金（給与・社会保険・労働保険）",
                     "basis": "bank_cash_payroll_category"}
        else:
            labor = {"amount": None, "source": None, "basis": "not_obtained"}
        total = sum(cats.values()) + (labor["amount"] or 0)
        out[month] = {"kind": "actual", "categories": cats, "labor": labor, "unclassified": round(m["unclassified"]),
                      "unclassified_count": m["unclassified_count"], "excluded_payroll_paid": round(m["excluded_payroll_paid"]),
                      "complete": bool(last_date) and last_date >= _month_end(month) and labor["amount"] is not None
                      and m["unclassified_count"] == 0,
                      "bank_through": min(last_date, _month_end(month)) if last_date else None,
                      "total_known": round(total)}
    return out


def baseline_forecast(actuals: Dict[str, Dict], months: List[str]) -> Dict[str, Dict]:
    """完了した実績月の直近最大3か月平均。根拠月を付ける。完了月が無ければ空。"""
    done = [k for k in sorted(actuals) if actuals[k]["complete"]][-BASELINE_MONTHS:]
    if not done:
        return {}
    cats = sorted({c for k in done for c in actuals[k]["categories"]})
    avg = {c: round(sum(actuals[k]["categories"].get(c, 0) for k in done) / len(done)) for c in cats}
    labor = round(sum(actuals[k]["labor"]["amount"] for k in done) / len(done))
    basis = f"直近の完了月{len(done)}か月（{done[0]}〜{done[-1]}）の費目別平均。人件費は同期間のMF給与実績平均"
    return {m: {"kind": "forecast", "model": "baseline_trailing_average", "basis": basis, "source_months": done,
                "categories": avg, "labor": {"amount": labor, "basis": "baseline_from_mf_payroll_actual"},
                "total_known": sum(avg.values()) + labor} for m in months if m not in actuals or not actuals[m]["complete"]}
