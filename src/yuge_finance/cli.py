"""yuge-finance CLI（喜らく単体 会計自動化）。"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from . import bi_refresh, config, csvio, db, locks, monthly, publish, publish_r2
from .accounting import breakeven_model, labor_model, reconciliation
from .ingest import (bank_actuals, bank_csv, cash_receipt_csv, loan_schedule,
                     manual_adjustments, opening_balance)
from .normalize.schema import (BankTransaction, BookingRecord, CashTransaction,
                               JournalEntry, ManualAdjustment)
from .reports import (bi_export, breakeven_report, exception_report, labor_report,
                      monthly_close_report, receipt_review_report,
                      revenue_reconciliation_report, validation_report)

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _validate_month(month: str) -> str:
    if not month or not MONTH_RE.match(month):
        raise SystemExit("ERROR: --month は YYYY-MM 形式で指定してください（例: 2026-07）")
    return month


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _print(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- init
def cmd_init(args) -> int:
    config.ensure_dirs()
    # サブディレクトリ作成
    for sub in ["imports/bank", "imports/cash_receipts/images",
                "imports/cash_receipts/csv", "imports/cash_receipts/reviewed",
                "imports/manual_adjustments", "imports/opening_balance",
                "data/raw/beds24", "data/raw/bank", "data/raw/cash_receipts",
                "data/raw/manual_adjustments", "data/raw/opening_balance",
                "data/staging/beds24_bookings", "data/staging/bank_transactions",
                "data/staging/cash_transactions", "data/staging/manual_adjustments",
                "data/staging/opening",
                "data/processed/journals", "data/processed/trial_balance",
                "data/processed/financial_statements", "data/output", "logs"]:
        (config.ROOT / sub).mkdir(parents=True, exist_ok=True)

    env = config.ROOT / ".env"
    if not env.exists() and (config.ROOT / ".env.example").exists():
        shutil.copy2(config.ROOT / ".env.example", env)
        _print(f"作成: {env}（Beds24トークン等を設定してください）")

    tpl = config.template_path()
    _print("=== yuge-finance init（喜らく単体）===")
    _print(f"プロジェクト: {config.ROOT}")
    _print(f"テンプレート: {'OK ' + str(tpl) if tpl.exists() else '見つかりません: ' + str(tpl)}")
    db.connect().close()
    _print(f"DB初期化: {config.DB_PATH}")
    _print("")
    _print("次の手順:")
    _print("  1. .env に BEDS24_REFRESH_TOKEN を設定")
    _print("  2. 銀行CSVを imports/bank/ に置く")
    _print("  3. 現金レシート画像を imports/cash_receipts/images/ に、CSVを csv/ に置く")
    _print("  4. yuge-finance close-month --month YYYY-MM")
    return 0


# ---------------------------------------------------------------- test-beds24-auth
def cmd_test_beds24_auth(args) -> int:
    from .api.beds24_client import Beds24Client, Beds24Error
    try:
        res = Beds24Client().test_auth()
    except Beds24Error as e:
        _print(f"success: False")
        _print(f"error: {e}")
        return 1
    _print("=== Beds24 認証テスト（Long Life Token方式）===")
    _print(f"success: {res['success']}")
    _print(f"auth_method: {res['auth_method']}")
    _print(f"property count: {res['property_count']}")
    for p in res["properties"]:
        _print(f"  property id: {p['id']} / property name: {p['name']}")
    _print("（token本体は表示しません）")
    return 0


# ---------------------------------------------------------------- debug-beds24-revenue
def cmd_debug_beds24_revenue(args) -> int:
    month = _validate_month(args.month)
    from .reports import beds24_audit
    try:
        res = beds24_audit.write_all(month)
    except FileNotFoundError as e:
        raise SystemExit(f"ERROR: {e}")
    _print(f"=== Beds24売上監査 {month} ===")
    _print(f"予約件数: {res['bookings']}")
    _print(f"Σ price 全件        : ¥{res['sum_price_all']:,}")
    _print(f"Σ price confirmedのみ: ¥{res['sum_price_confirmed_only']:,}")
    _print(f"Σ price confirmed+new: ¥{res['sum_price_confirmed_plus_new']:,}")
    _print(f"出力: {res['out_dir']}/")
    _print("  - beds24_revenue_field_audit.csv")
    _print("  - beds24_revenue_summary_by_status.csv")
    _print("  - beds24_revenue_summary_by_channel.csv")
    _print("  - beds24_raw_field_keys.json")
    return 0


def cmd_inspect_beds24_fields(args) -> int:
    """Beds24 raw payloadの実fieldを調査する（Phase 0）。個人情報は出力しない。"""
    from .reports import beds24_field_probe
    month = None if args.month in (None, "current") else _validate_month(args.month)
    out_path = beds24_field_probe.write(month)
    probe = json.loads(out_path.read_text(encoding="utf-8"))
    _print(f"=== Beds24 raw payload field probe ===")
    _print(f"予約件数(サンプル): {probe['booking_count_sampled']}")
    _print(f"status分布: {probe['status_value_counts']}")
    _print(f"cancelTime有り件数: {probe['cancel_time_present_count']}")
    _print(f"invoiceItems type分布: {probe['invoice_item_type_counts']}")
    for note in probe["notes"]:
        _print(f"  note: {note}")
    _print(f"出力: {out_path}（Gitには載せません）")
    return 0


# ---------------------------------------------------------------- fetch-beds24
def cmd_fetch_beds24(args, conn=None, soft_fail=False) -> Dict:
    month = _validate_month(args.month)
    own = conn is None
    conn = conn or db.connect()
    from .api.beds24_client import Beds24Client, Beds24Error
    try:
        records = Beds24Client().fetch_month(
            month, config.DATA_DIR / "raw" / "beds24" / month)
    except Beds24Error as e:
        if own:
            conn.close()
        if soft_fail:
            _print(f"[fetch-beds24] スキップ: {e}")
            return {"count": 0, "error": str(e)}
        raise SystemExit(f"ERROR: {e}")
    staging = config.DATA_DIR / "staging" / "beds24_bookings" / f"{month}.csv"
    csvio.write_dataclasses(staging, records, BookingRecord)
    stats = db.upsert(conn, "beds24_bookings", records)
    if own:
        conn.close()
    _print(f"[fetch-beds24] {month}: {len(records)}件取得 {stats}")
    return {"count": len(records), "staging": str(staging), **stats}


# ---------------------------------------------------------------- ingest-opening
def cmd_ingest_opening(args, conn=None) -> Dict:
    as_of_date = getattr(args, "date", None)
    res = opening_balance.run(conn, as_of_date=as_of_date)
    if not res.get("balanced", True) and res.get("opening_rows", 0) > 0:
        _print(f"[ingest-opening] 警告: 開始残高の借方≠貸方 "
               f"(借方={res['opening_debit']} 貸方={res['opening_credit']})")
    for c in res.get("critical_checks", []):
        tag = "❌ critical" if c["status"] == "critical" else "✅ OK"
        _print(f"[ingest-opening] {tag} {c['check']}: 値={c['value']} 期待={c['expected']}")
    if res.get("critical_failures", 0) > 0:
        _print(f"[ingest-opening] ❌ 開始残高がロック値と不一致（{res['critical_failures']}件）。"
               f"会計士確定書類を再確認してください。")
    _print(f"[ingest-opening] {res}")
    return res


# ---------------------------------------------------------------- ingest-bank
def cmd_ingest_bank(args, conn=None) -> Dict:
    month = _validate_month(args.month)
    res = bank_csv.run(month, conn)
    _print(f"[ingest-bank] {month}: {res}")
    return res


# ---------------------------------------------------------------- ingest-bank-csv
def cmd_ingest_bank_csv(args, conn=None) -> Dict:
    """銀行口座実績CSV取込（BI/分析専用。会計仕訳・PL/BS/CF・Excelは一切触らない）。

    --dry-run: 解析・残高検証・会計士BSとの照合のみ行い、DBには保存しない。
    --apply  : 上記に加えて bank_actual_transactions テーブルへ保存する。
    """
    own = conn is None
    conn = conn or db.connect()
    file_path = Path(args.file)
    if not file_path.exists():
        raise SystemExit(f"ERROR: ファイルが見つかりません: {file_path}")
    apply_ = bool(args.apply) and not bool(args.dry_run)
    res = bank_actuals.run(file_path, args.account, encoding=args.encoding,
                           month=args.month, apply=apply_, conn=conn)
    if own:
        conn.close()
    chain = res["balance_chain"]
    recon = res["reconciliation"]
    _print(f"=== ingest-bank-csv（{'apply' if apply_ else 'dry-run'}）===")
    _print(f"ファイル: {res['file']}")
    _print(f"行数: {res['rows_parsed']} / 新規保存: {res.get('inserted', 0)} / 重複スキップ: {res.get('skipped', 0)}")
    chain_status = "OK" if chain["balanced"] else f"NG({len(chain['mismatches'])}件)"
    _print(f"残高チェーン整合: {chain_status}")
    _print(f"取引前推定残高(先頭行より逆算): {chain['opening_balance_before_first_transaction']}")
    _print(f"会計士確定BSとの照合: {recon['bank_balance_reconciliation_status']} "
           f"(銀行実績={recon['bank_csv_observed_balance']}/{recon['bank_csv_observed_date']} "
           f"会計士確定={recon['accountant_bs_cash_balance']} 差異={recon['bank_vs_accountant_difference']})")
    if not apply_:
        _print("（dry-runのためDBへは保存していません。--apply で確定保存してください）")
    return res


# ---------------------------------------------------------------- ingest-cash
def cmd_ingest_cash(args, conn=None) -> Dict:
    month = _validate_month(args.month)
    own = conn is None
    conn = conn or db.connect()
    res = cash_receipt_csv.run(month, conn)
    # receipt_review_report
    cash_txns = cash_receipt_csv.load(month)
    issues = cash_receipt_csv.reconcile_images(cash_txns)
    missing = {i["receipt_file"] for i in issues}
    out = config.output_dir(month) / "receipt_review_report.csv"
    receipt_review_report.write(cash_txns, missing, out)
    if own:
        conn.close()
    _print(f"[ingest-cash] {month}: {res} / review_report={out}")
    res["receipt_review_report"] = str(out)
    return res


# ---------------------------------------------------------------- ingest-adjustments
def cmd_ingest_adjustments(args, conn=None) -> Dict:
    month = _validate_month(args.month)
    res = manual_adjustments.run(month, conn)
    _print(f"[ingest-adjustments] {month}: {res}")
    return res


# ---------------------------------------------------------------- ingest-loan-schedule
def cmd_ingest_loan_schedule(args, conn=None) -> Dict:
    """月次債務返済予定表取込（Phase B）。予定表が無ければ「予定表未投入」で終了(criticalにしない)。"""
    month = _validate_month(args.month)
    res = loan_schedule.run(month, conn)
    _print(f"[ingest-loan-schedule] {month}: status={res['status']} "
           f"件数={res['count']} critical={res['critical_issues']}")
    for issue in res.get("issues", []):
        _print(f"  {issue['severity']}: {issue['issue']} loan_id={issue.get('loan_id')} "
               f"value={issue.get('value')}")
    return res


# ---------------------------------------------------------------- build-labor-forecast
def cmd_build_labor_forecast(args, conn=None) -> Dict:
    """Beds24日別稼働から月次人件費を速報推計する（Phase C）。給与確定仕訳ではない。"""
    month = _validate_month(args.month)
    own = conn is None
    conn = conn or db.connect()
    bookings = db.load_objects(conn, "beds24_bookings", month, "checkin_date")
    for r in bookings:
        r.finalize()
    result = labor_model.build(month, bookings)
    out = labor_report.write(month, result, config.output_dir(month))
    if own:
        conn.close()
    _print(f"[build-labor-forecast] {month}: "
           f"total_base={result['labor_total_base_case']} "
           f"low={result['labor_total_low_case']} high={result['labor_total_high_case']} "
           f"occupied_days={result['labor_occupied_days']} status={result['labor_model_status']}")
    return {**result, **out}


# ---------------------------------------------------------------- build-breakeven
def cmd_build_breakeven(args, conn=None) -> Dict:
    """開始残高・固定費変動費モデル・人件費予測・Beds24速報売上から損益分岐点を算出する（Phase D）。"""
    month = _validate_month(args.month)
    own = conn is None
    conn = conn or db.connect()
    ctx = monthly.assemble(month, conn)
    bm = ctx["breakeven_model"]
    exclude = config.kiraku().get("revenue", {}).get("exclude_statuses",
                                                     ["cancelled", "canceled", "black"])
    out = breakeven_report.write(month, bm, ctx["bookings"], exclude, config.output_dir(month),
                                 pace=ctx.get("pace_model"))
    if own:
        conn.close()
    pace = ctx.get("pace_model", {})
    _print(f"[build-breakeven] {month}: "
           f"cash_bep={bm['cash_operating_breakeven_revenue']} "
           f"cash_rate={bm['cash_operating_breakeven_achievement_rate']} "
           f"accounting_bep={bm['accounting_operating_breakeven_revenue']} "
           f"finance_bep={bm['finance_breakeven_revenue']} "
           f"status={bm['breakeven_model_status']} "
           f"pace={pace.get('booking_pace_status')}({pace.get('booking_pace_label')})")
    return {**bm, **pace, **out}


# ---------------------------------------------------------------- build-ledger
def cmd_build_ledger(args, conn=None) -> Dict:
    month = _validate_month(args.month)
    own = conn is None
    conn = conn or db.connect()
    ctx = monthly.assemble(month, conn)
    confirmed: List[JournalEntry] = ctx["confirmed"]
    exceptions: List[JournalEntry] = ctx["exceptions"]

    db.replace_journal_for_month(conn, month, confirmed)

    # 確定仕訳CSV
    proc = config.DATA_DIR / "processed" / "journals" / f"{month}.csv"
    csvio.write_dataclasses(proc, confirmed, JournalEntry)
    # 例外レポート
    exc = config.output_dir(month) / "exception_report.csv"
    exception_report.write(exceptions, exc)

    res = {
        "confirmed": len(confirmed), "exceptions": len(exceptions),
        "by_source": ctx["journal"]["by_source"],
        "balanced": abs(ctx["debit_total"] - ctx["credit_total"]) <= 0.5,
        "journal_csv": str(proc), "exception_report": str(exc),
    }
    if own:
        conn.close()
    _print(f"[build-ledger] {month}: {res}")
    return res


# ---------------------------------------------------------------- export-excel
def cmd_export_excel(args, conn=None) -> Dict:
    month = _validate_month(args.month)
    own = conn is None
    conn = conn or db.connect()
    from .excel.workbook_writer import WorkbookWriter
    from .excel.workbook_validator import validate as wb_validate

    out_path = config.output_dir(month) / "updated_workbook.xlsx"
    ctx = monthly.assemble(month, conn, workbook_path=out_path)
    confirmed = ctx["confirmed"]

    bank_rows = [_bank_row(t) for t in ctx["bank_txns"]]
    beds24_rows = _beds24_rows(ctx["bookings"])
    checks_preview = []  # チェックブロックは後で完全版を入れる

    writer = WorkbookWriter()
    out_path = writer.write(
        month, confirmed, bank_rows, beds24_rows,
        loan_rollforward=ctx["loan_rollforward"],
        checks=None, revenue_recon=ctx["revenue_recon"], output_path=out_path)

    wb_checks = wb_validate(out_path)
    res = {"workbook": str(out_path),
           "journal_rows": len(confirmed),
           "bank_rows": len(bank_rows), "beds24_rows": len(beds24_rows),
           "wb_checks_ok": all(c["status"] == "OK" for c in wb_checks)}
    if own:
        conn.close()
    _print(f"[export-excel] {month}: {res}")
    return res


def _bank_row(t: BankTransaction) -> dict:
    return {
        "transaction_date": t.transaction_date, "bank_name": t.bank_name,
        "account_name": t.account_name, "description": t.description,
        "deposit_amount": t.deposit_amount or "", "withdrawal_amount": t.withdrawal_amount or "",
        "balance": t.balance, "classification_status": "",
        "suggested_account": "", "counterparty": t.counterparty, "raw_memo": t.raw_memo,
    }


def _beds24_rows(bookings: List[BookingRecord]) -> List[dict]:
    cfg = config.kiraku()
    exclude = cfg.get("revenue", {}).get("exclude_statuses", ["cancelled"])
    rows = []
    for b in bookings:
        if b.is_cancelled(exclude):
            continue
        rows.append({
            "stay_month": b.stay_month, "booking_id": b.booking_id, "channel": b.channel,
            "checkin_date": b.checkin_date, "stay_nights": b.stay_nights, "rooms": b.rooms,
            "guests": b.guests, "gross_revenue": b.gross_revenue,
            "ota_commission": b.ota_commission, "net_revenue": b.net_revenue,
            "status": b.status, "memo": b.guest_name,
        })
    return rows


# ---------------------------------------------------------------- refresh-beds24-bi
def cmd_refresh_beds24_bi(args) -> int:
    start = bi_refresh.current_month() if args.month == "current" else _validate_month(args.month)
    months = bi_refresh.month_list(start, args.months)
    do_publish = bool(args.publish) and not bool(args.no_publish)
    do_publish_r2 = bool(args.publish_r2) and not bool(args.no_publish_r2)
    auto_months = bool(getattr(args, "auto_months_with_bookings", False))

    lock = locks.FileLock(config.LOG_DIR / "beds24_bi_refresh.lock", stale_seconds=3600)
    try:
        lock.acquire()
    except locks.LockError as e:
        _print(f"[refresh-beds24-bi] スキップ（多重起動防止）: {e}")
        return 0
    try:
        label = "自動抽出（予約が1件でもある月）" if auto_months else months
        _print(f"=== refresh-beds24-bi 対象={label} dry_run={bool(args.dry_run)} ===")
        status = bi_refresh.refresh(months, dry_run=bool(args.dry_run),
                                    auto_months_with_bookings=auto_months)
        _print(f"[refresh-beds24-bi] 成功月={status['success_months']} エラー={len(status['errors'])} "
               f"revenue_data_status={status.get('revenue_data_status')}")
        _print(f"[refresh-beds24-bi] default_month={status.get('default_month')} "
               f"available_months={status.get('available_months')} "
               f"months_with_any_booking={status.get('months_with_any_booking')} "
               f"months_with_active_booking={status.get('months_with_active_booking')}")
        for e in status["errors"]:
            _print(f"  ERROR {e['month']}/{e['stage']}: {e['error']}")
        if status["errors"]:
            _print("[refresh-beds24-bi] APIエラーのため publish しません（既存BIは保持）。")
            return 1
        if do_publish and not args.dry_run:
            try:
                res = publish.publish()
                _print(f"[publish-bi] {res}")
            except publish.PublishError as pe:
                _print(f"[publish-bi] 警告: publish失敗（ローカルBIは成功）: {pe}")
        if do_publish_r2 and not args.dry_run:
            try:
                res = publish_r2.publish()
                _print(f"[publish-bi-r2] {res}")
            except publish_r2.PublishR2Error as pe:
                _print(f"[publish-bi-r2] 警告: R2アップロード失敗（ローカルBIは成功）: {pe}")
        return 0
    finally:
        lock.release()


# ---------------------------------------------------------------- publish-bi
def cmd_publish_bi(args) -> int:
    try:
        res = publish.publish()
    except publish.PublishError as e:
        _print(f"[publish-bi] エラー: {e}")
        return 1
    _print(f"[publish-bi] 公開完了: {res['published']}ファイル → {res['dst']}")
    _print(f"  manifest checksum: {res['checksum'][:16]}…")
    return 0


# ---------------------------------------------------------------- publish-bi-r2
def cmd_publish_bi_r2(args) -> int:
    """data/output/latest/bi/ の6ファイルをR2 latest/へアップロード（Worker本体はdeployしない）。"""
    source_dir = Path(args.source_dir) if args.source_dir else publish_r2.default_source_dir()
    try:
        res = publish_r2.publish(source_dir=source_dir, bucket=args.bucket, prefix=args.prefix,
                                 dry_run=bool(args.dry_run))
    except publish_r2.PublishR2Error as e:
        _print(f"[publish-bi-r2] エラー: {e}")
        return 1
    if res["dry_run"]:
        _print(f"[publish-bi-r2] dry-run: bucket={res['bucket']} prefix={res['prefix']}")
        for k in res["would_upload_keys"]:
            _print(f"  would upload: {k}")
        _print(f"  generated_at_jst: {res['generated_at_jst']}")
    else:
        _print(f"[publish-bi-r2] 完了: uploaded={res['uploaded_count']} "
               f"bucket={res['bucket']} prefix={res['prefix']}")
        for k in res["uploaded_keys"]:
            _print(f"  uploaded: {k}")
        _print(f"  generated_at_jst: {res['generated_at_jst']}")
    return 0


# ---------------------------------------------------------------- close-month
def cmd_close_month(args) -> int:
    month = _validate_month(args.month)
    conn = db.connect()
    log: List[Dict] = []

    def step(name, fn):
        try:
            res = fn()
            log.append({"step": name, "status": "ok", "result": res, "time": _now()})
            return res
        except SystemExit as e:
            log.append({"step": name, "status": "error", "error": str(e), "time": _now()})
            _print(f"[{name}] エラー: {e}")
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001 - 自己改善ループ用に握って継続
            log.append({"step": name, "status": "error", "error": str(e),
                        "trace": traceback.format_exc(), "time": _now()})
            _print(f"[{name}] 例外: {e}")
            return {"error": str(e)}

    _print(f"=== close-month {month}（喜らく単体）===")
    step("ingest-opening", lambda: cmd_ingest_opening(args, conn))
    step("fetch-beds24", lambda: cmd_fetch_beds24(args, conn, soft_fail=True))
    step("ingest-bank", lambda: cmd_ingest_bank(args, conn))
    step("ingest-cash", lambda: cmd_ingest_cash(args, conn))
    step("ingest-adjustments", lambda: cmd_ingest_adjustments(args, conn))
    step("build-ledger", lambda: cmd_build_ledger(args, conn))
    step("export-excel", lambda: cmd_export_excel(args, conn))

    # 全出力 + レポート
    out = step("reports", lambda: _write_all_outputs(month, conn, log))

    # processing_log
    plog = config.output_dir(month) / "processing_log.json"
    plog.write_text(json.dumps(log, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")
    conn.close()

    all_ok = out.get("all_ok", False) if isinstance(out, dict) else False
    _print("")
    _print(f"=== 完了: data/output/{month}/ ===")
    _print(f"総合判定: {'✅ 重大エラーなし' if all_ok else '❌ validation_report.md を確認'}")
    return 0 if all_ok else 1


def _write_all_outputs(month: str, conn, log: List[Dict]) -> Dict:
    from .excel.workbook_validator import validate as wb_validate
    out_dir = config.output_dir(month)
    wb_path = out_dir / "updated_workbook.xlsx"
    ctx = monthly.assemble(month, conn, workbook_path=wb_path if wb_path.exists() else None)

    # 画像照合件数を反映
    img_issues = cash_receipt_csv.reconcile_images(ctx["cash_txns"])
    missing = {i["receipt_file"] for i in img_issues}
    ctx["image_issues"] = len(img_issues)

    # エンティティCSV
    csvio.write_dataclasses(out_dir / "beds24_bookings.csv", ctx["bookings"], BookingRecord)
    csvio.write_dataclasses(out_dir / "bank_transactions.csv", ctx["bank_txns"], BankTransaction)
    csvio.write_dataclasses(out_dir / "cash_transactions.csv", ctx["cash_txns"], CashTransaction)
    csvio.write_dataclasses(out_dir / "manual_adjustments.csv", ctx["manual"], ManualAdjustment)
    csvio.write_dataclasses(out_dir / "journal_entries.csv", ctx["confirmed"], JournalEntry)

    # 試算表 / 3表 / ロールフォワード
    csvio.write_rows(out_dir / "trial_balance.csv", ctx["tb"],
                     ["code", "type", "account", "debit_total", "credit_total", "net", "statement"])
    csvio.write_rows(out_dir / "pl_summary.csv", ctx["pl"]["lines"], ["item", "amount"])
    csvio.write_rows(out_dir / "bs_summary.csv", ctx["bs"]["lines"], ["item", "amount"])
    csvio.write_rows(out_dir / "cf_summary.csv", ctx["cf"]["lines"], ["item", "amount"])
    cr = ctx["cash_rollforward"]
    csvio.write_rows(out_dir / "cash_balance_rollforward.csv", [cr], list(cr.keys()))

    # 例外 / レシート確認 / 売上reconciliation
    exception_report.write(ctx["exceptions"], out_dir / "exception_report.csv")
    receipt_review_report.write(ctx["cash_txns"], missing, out_dir / "receipt_review_report.csv")
    revenue_reconciliation_report.write(month, ctx["revenue_recon"], out_dir)

    # 検証
    checks = reconciliation.run(month, ctx)
    wb_checks = wb_validate(wb_path) if wb_path.exists() else [
        {"check": "出力ファイル存在", "status": "要確認", "detail": "未生成"}]
    sev = reconciliation.severity(checks)
    validation_report.write(month, checks, wb_checks, sev,
                            out_dir / "validation_report.md")

    # 月次締めレポート
    summary = {
        "beds24_count": len(ctx["bookings"]),
        "revenue_bookings": ctx["journal"]["by_source"]["beds24"],
        "bank_count": len(ctx["bank_txns"]), "cash_count": len(ctx["cash_txns"]),
        "cash_approved": sum(1 for t in ctx["cash_txns"] if t.review_status == "approved"),
        "manual_count": len(ctx["manual"]),
        "confirmed": len(ctx["confirmed"]), "exceptions": len(ctx["exceptions"]),
        "debit_total": ctx["debit_total"], "credit_total": ctx["credit_total"],
        "pl": ctx["pl"], "bs": ctx["bs"], "cf": ctx["cf"],
        "output_dir": str(out_dir), "workbook": str(wb_path),
        "all_ok": sev["all_ok"] and all(c["status"] == "OK" for c in wb_checks),
    }
    monthly_close_report.write(month, summary, out_dir / "monthly_close_report.md")

    # BI出力
    bi_export.write_all(month, ctx, checks, wb_checks, sev, out_dir, conn=conn)
    return {"all_ok": summary["all_ok"], "critical": len(sev["critical"]),
            "warnings": len(sev["warnings"])}


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yuge-finance",
                                description="喜らく単体 会計・財務モデル自動更新")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="ディレクトリ・config・DB初期化")
    io_p = sub.add_parser("ingest-opening",
                          help="開始残高(5月末)・会計士YTD・KPI参照取込")
    io_p.add_argument("--date", default=None, help="開始残高基準日 YYYY-MM-DD（任意）")
    sub.add_parser("test-beds24-auth",
                   help="Beds24 Long Life Token 認証テスト（properties取得）")
    sub.add_parser("publish-bi", help="latest BIをCloudflare公開ディレクトリへ反映")

    pr = sub.add_parser("publish-bi-r2",
                        help="latest BIをR2(kiraku-bi-data)へアップロード（Worker本体はdeployしない）")
    pr.add_argument("--source-dir", default=None, help="既定: data/output/latest/bi")
    pr.add_argument("--bucket", default=publish_r2.DEFAULT_BUCKET, help="R2 bucket名")
    pr.add_argument("--prefix", default=publish_r2.DEFAULT_PREFIX, help="R2 key prefix")
    pr.add_argument("--dry-run", action="store_true", help="アップロードせず対象を列挙")

    rb = sub.add_parser("refresh-beds24-bi",
                        help="Beds24速報BIの巡回更新（仕訳/Excelは触らない）")
    rb.add_argument("--month", default="current", help="current または YYYY-MM")
    rb.add_argument("--months", type=int, default=2, help="対象月数（当月から）")
    rb.add_argument("--auto-months-with-bookings", action="store_true",
                    help="Beds24予約が1件でもある月（宿泊日ベース、キャンセル含む）を自動抽出して対象にする"
                         "（指定時は--month/--monthsより優先）")
    rb.add_argument("--publish", action="store_true", help="生成後にCloudflare公開ディレクトリへ反映")
    rb.add_argument("--no-publish", action="store_true", help="公開しない（--publishを上書き）")
    rb.add_argument("--publish-r2", action="store_true",
                    help="生成後にR2(kiraku-bi-data)へアップロード（手動検証用。まだlaunchdには組み込まない）")
    rb.add_argument("--no-publish-r2", action="store_true",
                    help="R2アップロードしない（--publish-r2を上書き）")
    rb.add_argument("--dry-run", action="store_true", help="ファイルを書かず検証のみ")

    def add_month(name, help_):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--month", required=True, help="対象月 YYYY-MM")
        return sp

    add_month("fetch-beds24", "Beds24予約取得")
    add_month("debug-beds24-revenue", "Beds24売上フィールド監査")
    ib = sub.add_parser("inspect-beds24-fields", help="Beds24 raw payloadの実field調査（Phase 0）")
    ib.add_argument("--month", default="current", help="対象月 YYYY-MM。省略/currentで全月")
    add_month("ingest-bank", "銀行CSV取込")
    ibc = sub.add_parser("ingest-bank-csv",
                         help="銀行口座実績CSV取込（BI/分析専用。手動実行。15分速報更新では実行しない）")
    ibc.add_argument("--file", required=True, help="取込む銀行CSVのパス")
    ibc.add_argument("--account", required=True, help="口座キー（例: 蔵王支店_0036041）")
    ibc.add_argument("--encoding", default="auto", help="文字コード（既定: auto=cp932/utf-8自動判別）")
    ibc.add_argument("--dry-run", action="store_true", help="DBへ保存せず解析・検証結果のみ表示")
    ibc.add_argument("--apply", action="store_true", help="解析結果をDBへ保存する")
    ibc.add_argument("--month", default=None, help="レポートで先頭表示する対象月 YYYY-MM（任意）")
    add_month("ingest-cash", "現金レシートCSV取込")
    add_month("ingest-adjustments", "手動補正CSV取込")
    add_month("ingest-loan-schedule", "月次債務返済予定表取込")
    add_month("build-labor-forecast", "Beds24日別稼働から人件費速報推計")
    add_month("build-breakeven", "固定費・変動費モデルから損益分岐点算出")
    add_month("build-ledger", "仕訳生成")
    ee = add_month("export-excel", "Excel出力")
    ee.add_argument("--template", default=None, help="テンプレパス(任意)")
    cm = add_month("close-month", "月次締め一括")
    cm.add_argument("--template", default=None, help="テンプレパス(任意)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cmd = args.command
    if cmd == "init":
        return cmd_init(args)
    if cmd == "test-beds24-auth":
        return cmd_test_beds24_auth(args)
    if cmd == "refresh-beds24-bi":
        return cmd_refresh_beds24_bi(args)
    if cmd == "publish-bi":
        return cmd_publish_bi(args)
    if cmd == "publish-bi-r2":
        return cmd_publish_bi_r2(args)
    handlers = {
        "ingest-opening": lambda: (cmd_ingest_opening(args), 0)[1],
        "debug-beds24-revenue": lambda: cmd_debug_beds24_revenue(args),
        "inspect-beds24-fields": lambda: cmd_inspect_beds24_fields(args),
        "fetch-beds24": lambda: (cmd_fetch_beds24(args), 0)[1],
        "ingest-bank": lambda: (cmd_ingest_bank(args), 0)[1],
        "ingest-bank-csv": lambda: (cmd_ingest_bank_csv(args), 0)[1],
        "ingest-cash": lambda: (cmd_ingest_cash(args), 0)[1],
        "ingest-adjustments": lambda: (cmd_ingest_adjustments(args), 0)[1],
        "ingest-loan-schedule": lambda: (cmd_ingest_loan_schedule(args), 0)[1],
        "build-labor-forecast": lambda: (cmd_build_labor_forecast(args), 0)[1],
        "build-breakeven": lambda: (cmd_build_breakeven(args), 0)[1],
        "build-ledger": lambda: (cmd_build_ledger(args), 0)[1],
        "export-excel": lambda: (cmd_export_excel(args), 0)[1],
        "close-month": lambda: cmd_close_month(args),
    }
    return handlers[cmd]()


if __name__ == "__main__":
    sys.exit(main())
