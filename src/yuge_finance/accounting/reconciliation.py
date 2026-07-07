"""検証・整合性チェック（喜らく単体）。要件セクション9を網羅する。"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .. import config
from ..normalize import validators
from ..normalize.schema import JournalEntry

TOL = 0.5
OK, NG, WARN = "OK", "要確認", "WARN"


def _chk(name, target, value, allow, ok, detail="") -> Dict:
    return {"check": name, "target": target, "value": value, "allow": allow,
            "status": OK if ok else NG, "detail": detail}


def cash_rollforward(month: str, cash_txns: List, opening: float = 0.0) -> Dict:
    inflow = sum(t.amount for t in cash_txns if t.transaction_type == "現金入金")
    outflow = sum(t.amount for t in cash_txns if t.transaction_type == "現金支払")
    move = sum(t.amount for t in cash_txns if t.transaction_type == "現金移動")
    reimburse = sum(t.amount for t in cash_txns if t.transaction_type == "立替精算")
    closing = opening + inflow - outflow - move - reimburse
    return {"month": month, "opening": round(opening, 2), "現金入金": round(inflow, 2),
            "現金支払": round(outflow, 2), "現金移動": round(move, 2),
            "立替精算": round(reimburse, 2), "closing": round(closing, 2)}


def loan_rollforward(month: str, entries: List[JournalEntry], opening: float = 0.0) -> Dict:
    new_borrow = sum(e.credit_amount for e in entries if e.credit_account == "借入金")
    repay = sum(e.debit_amount for e in entries if e.debit_account == "借入金")
    closing = opening + new_borrow - repay
    return {"month": month, "opening": round(opening, 2), "新規借入": round(new_borrow, 2),
            "元本返済": round(repay, 2), "closing": round(closing, 2)}


def run(month: str, ctx: Dict) -> List[Dict]:
    checks: List[Dict] = []
    bookings = ctx.get("bookings", [])
    bank = ctx.get("bank_txns", [])
    cash = ctx.get("cash_txns", [])
    confirmed = ctx.get("confirmed", [])
    exceptions = ctx.get("exceptions", [])
    tb_totals = ctx.get("tb_totals", {})
    bs = ctx.get("bs", {})
    cf = ctx.get("cf", {})

    # 0. 開始残高（会計士確定BS 2026-05-31）ロック値との一致。不一致はcritical。
    for c in ctx.get("opening_critical", []):
        checks.append(_chk(c["check"], "opening_balance", c["value"], c["expected"],
                           c["status"] == "OK"))

    # 0b. 月次債務返済（Phase B）: liability_account不正値 / principal+interest!=total はcritical。
    # 銀行明細未一致（予定表未投入含む）はcriticalにしない（正常な未確定状態）。
    debt_critical_rules = {"debt_internal_mismatch", "debt_invalid_liability_account"}
    for e in ctx.get("debt_detail", {}).get("exceptions", []):
        if e.rule_id in debt_critical_rules:
            checks.append(_chk(f"債務返済検証:{e.rule_id}", "debt", e.memo, "整合",
                               False, f"loan_id={e.source_id}"))

    # 1. Beds24 予約ID 重複なし
    ids = [b.booking_id for b in bookings]
    dup_b = len(ids) - len(set(ids))
    checks.append(_chk("Beds24予約ID重複", "beds24", dup_b, 0, dup_b == 0))

    # 2. 銀行CSV 重複なし
    bh = [t.import_hash for t in bank]
    dup_bank = len(bh) - len(set(bh))
    checks.append(_chk("銀行明細重複", "bank", dup_bank, 0, dup_bank == 0))

    # 3. 現金CSV 重複なし
    ch = [t.import_hash for t in cash]
    dup_cash = len(ch) - len(set(ch))
    checks.append(_chk("現金明細重複", "cash", dup_cash, 0, dup_cash == 0))

    # 4. 現金レシート原本照合
    img_issues = ctx.get("image_issues", 0)
    checks.append(_chk("レシート原本照合", "cash", img_issues, 0, img_issues == 0,
                       "receipt_file と images/ の不一致件数"))

    # 5. approved以外の現金取引が確定仕訳に入っていない
    approved_ids = {t.cash_transaction_id for t in cash if t.review_status == "approved"}
    leaked = [e for e in confirmed if e.source == "cash" and e.source_id not in approved_ids]
    checks.append(_chk("未承認現金の確定混入", "cash", len(leaked), 0, len(leaked) == 0))

    # 6. 仕訳の借方合計=貸方合計
    ok_j, jd, jc = validators.journal_balanced(confirmed)
    checks.append(_chk("仕訳貸借一致", "journal", round(jd - jc, 2), 0, ok_j,
                       f"借方={round(jd)} 貸方={round(jc)}"))

    # 7. 試算表の借方合計=貸方合計
    checks.append(_chk("試算表貸借一致", "trial_balance",
                       round(tb_totals.get("debit", 0) - tb_totals.get("credit", 0), 2), 0,
                       tb_totals.get("balanced", False)))

    # 8. BS 資産=負債純資産
    checks.append(_chk("BSバランス", "bs",
                       round(bs.get("assets", 0) - bs.get("liabilities_equity", 0), 2), 0,
                       bs.get("balanced", False)))

    # 9. CF現預金増減=BS現預金増減
    checks.append(_chk("CF整合", "cf", 0 if cf.get("reconciles") else 1, 0,
                       cf.get("reconciles", False),
                       f"現預金純増減={cf.get('cash_change', 0)}"))

    # 10. 未分類明細件数（仮勘定/unmatched）
    unclassified = [e for e in confirmed + exceptions if e.rule_id == "unmatched"]
    checks.append(_chk("未分類銀行明細", "bank", len(unclassified), 0, len(unclassified) == 0,
                       "WARN: 要手動分類" if unclassified else ""))

    # 11. medium/low confidence件数
    ml = [e for e in exceptions]
    checks.append(_chk("medium/low件数", "journal", len(ml), 0, len(ml) == 0,
                       "例外レポート参照"))

    # 12. 対象月外データ混入件数
    oob = (validators.out_of_month_count(bank, "transaction_date", month)
           + validators.out_of_month_count(cash, "transaction_date", month))
    checks.append(_chk("対象月外データ", "all", oob, 0, oob == 0,
                       "取込時に月で絞り込み済み"))

    # 13. 借入ロールフォワード整合（再計算一致）
    lr = ctx.get("loan_rollforward", {})
    if lr:
        recomputed = lr["opening"] + lr["新規借入"] - lr["元本返済"]
        checks.append(_chk("借入ロールフォワード", "loan",
                           round(lr["closing"] - recomputed, 2), 0,
                           abs(lr["closing"] - recomputed) <= TOL))

    # 14. 現金残高ロールフォワード整合
    cr = ctx.get("cash_rollforward", {})
    if cr:
        recomputed = (cr["opening"] + cr["現金入金"] - cr["現金支払"]
                      - cr["現金移動"] - cr["立替精算"])
        checks.append(_chk("現金ロールフォワード", "cash",
                           round(cr["closing"] - recomputed, 2), 0,
                           abs(cr["closing"] - recomputed) <= TOL))

    # 14b. 売上データ状態（情報表示のみ）。
    # Beds24宿泊月売上 と 銀行入金月実績 は同月比較しない（OTA精算ラグ）。
    # 同月差分は warning にも critical にもしない。
    rr = ctx.get("revenue_recon")
    if rr:
        checks.append({
            "check": "売上データ状態", "target": "revenue",
            "value": rr["revenue_data_status"],
            "allow": "同月比較対象外",
            "status": OK,   # 常にOK（差分は異常ではない）
            "detail": (f"A宿泊月速報¥{rr['beds24_stay_month_revenue_excluding_cancelled']:,} / "
                       f"B入金月OTA¥{rr['bank_deposit_month_ota_revenue']:,}"
                       f"（{rr['revenue_comparison_status']}）"),
        })

    # 15. Excel出力存在 & テンプレ未上書き
    wb = ctx.get("workbook_path")
    if wb is not None:
        exists = Path(wb).exists()
        checks.append(_chk("Excel出力存在", "excel", "あり" if exists else "なし", "あり", exists))
        tpl = config.template_path()
        tpl_ok = Path(wb).resolve() != tpl.resolve()
        checks.append(_chk("テンプレ未上書き", "excel", "OK" if tpl_ok else "上書き", "OK", tpl_ok))

    return checks


def severity(checks: List[Dict]) -> Dict:
    # 件数系（未分類/例外）はWARN扱いにし、整合性NGのみ重大とする
    info_checks = {"未分類銀行明細", "medium/low件数"}
    critical = [c for c in checks if c["status"] != OK and c["check"] not in info_checks]
    warnings = [c for c in checks if c["status"] != OK and c["check"] in info_checks]
    return {"critical": critical, "warnings": warnings,
            "all_ok": len(critical) == 0}
