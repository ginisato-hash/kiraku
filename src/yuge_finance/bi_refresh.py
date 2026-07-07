"""Beds24速報BIの定期巡回更新（喜らく単体）。

15分おきに想定。Beds24取得とBIファイル再生成（人件費予測・損益分岐点を含む）のみ行う。
仕訳生成・PL/BS/CF確定・Excel出力・銀行/現金/手動取込・close-month は一切行わない。
APIエラー時は既存BIファイルを壊さない。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

from . import config, csvio, db, monthly
from .accounting import reconciliation
from .normalize.schema import BookingRecord
from .reports import bi_export, breakeven_report, labor_report

JST = timezone(timedelta(hours=9))
BI_FILES = ["bi_snapshot.json", "bi_daily_timeseries.csv",
            "bi_validation_status.json", "bi_exception_summary.json"]


def jst_now() -> datetime:
    return datetime.now(JST)


def jst_str() -> str:
    return jst_now().isoformat(timespec="seconds")


def current_month() -> str:
    return jst_now().strftime("%Y-%m")


def month_list(start: str, n: int) -> List[str]:
    y, m = (int(x) for x in start.split("-"))
    out = []
    for i in range(max(1, n)):
        mm = m + i
        yy = y + (mm - 1) // 12
        mm = (mm - 1) % 12 + 1
        out.append(f"{yy:04d}-{mm:02d}")
    return out


def _fetch_beds24(month: str, conn) -> int:
    from .api.beds24_client import Beds24Client
    raw_dir = config.DATA_DIR / "raw" / "beds24" / month
    records = Beds24Client().fetch_month(month, raw_dir)
    staging = config.DATA_DIR / "staging" / "beds24_bookings" / f"{month}.csv"
    csvio.write_dataclasses(staging, records, BookingRecord)
    db.upsert(conn, "beds24_bookings", records)
    return len(records)


def _bi_dir(month_or_name: str) -> Path:
    return config.DATA_DIR / "output" / month_or_name / "bi"


def _copy_bi(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for fn in BI_FILES:
        s = src / fn
        if s.exists():
            tmp = dst / (fn + ".tmp")
            shutil.copy2(s, tmp)
            tmp.replace(dst / fn)   # atomic


def _atomic_write_json(path: Path, obj: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str),
                   encoding="utf-8")
    tmp.replace(path)


def _load_prev_status() -> Dict:
    p = _bi_dir("latest") / "bi_refresh_status.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def refresh(months: List[str], dry_run: bool = False, conn=None) -> Dict:
    own = conn is None
    conn = conn or db.connect()
    started = jst_str()
    success: List[str] = []
    errors: List[Dict] = []
    month_ctxs: List[Dict] = []

    for m in months:
        try:
            n = _fetch_beds24(m, conn)
        except Exception as e:  # noqa: BLE001 - 既存BIを壊さず継続
            errors.append({"month": m, "stage": "fetch", "error": str(e)})
            continue
        ctx = monthly.assemble(m, conn)          # 読み取り計算のみ（永続化しない）
        checks = reconciliation.run(m, ctx)      # workbook_path無し→Excelチェック無し
        sev = reconciliation.severity(checks)
        if not dry_run:
            out_dir = config.DATA_DIR / "output" / m
            bi_export.write_all(m, ctx, checks, [], sev, out_dir)
            # build-labor-forecast / build-breakeven 相当（速報。仕訳/Excelは書かない）
            labor_report.write(m, ctx["labor_forecast"], out_dir)
            exclude = config.kiraku().get("revenue", {}).get(
                "exclude_statuses", ["cancelled", "canceled", "black"])
            breakeven_report.write(m, ctx["breakeven_model"], ctx["bookings"], exclude, out_dir,
                                   pace=ctx.get("pace_model"))
        month_ctxs.append(ctx)
        success.append(m)

    cur = months[0]
    # current/latest は当月取得成功時のみ更新（失敗時は既存維持）
    if not dry_run and cur in success:
        for name in ("current", "latest"):
            _copy_bi(_bi_dir(cur), _bi_dir(name))
            bi_export.write_monthly_kpi(month_ctxs, _bi_dir(name) / "bi_monthly_kpi.csv")

    # ステータス（直近成功/エラー時刻を保持）
    prev = _load_prev_status()
    status = {
        "started_at_jst": started,
        "finished_at_jst": jst_str(),
        "source_months": months,
        "success_months": success,
        "beds24_last_fetch_at_jst": jst_str() if success else prev.get("beds24_last_fetch_at_jst"),
        "last_success_at_jst": jst_str() if not errors else prev.get("last_success_at_jst"),
        "last_error_at_jst": jst_str() if errors else prev.get("last_error_at_jst"),
        "errors": errors,
        "revenue_data_status": (month_ctxs[0]["revenue_recon"]["revenue_data_status"]
                                if month_ctxs else prev.get("revenue_data_status")),
        "ok": len(errors) == 0,
    }
    if not dry_run and cur in success:
        for name in ("current", "latest"):
            _atomic_write_json(_bi_dir(name) / "bi_refresh_status.json", status)
            # publish-bi-r2 が data/output/latest/bi/manifest.json をそのまま読めるように、
            # ここでも簡易manifestを生成しておく（Cloudflare公開用の詳細manifestはpublish.publish()側）。
            snap0 = month_ctxs[0] if month_ctxs else {}
            rr0 = snap0.get("revenue_recon", {})
            _atomic_write_json(_bi_dir(name) / "manifest.json", {
                "generated_at_jst": jst_str(),
                "source_months": months,
                "beds24_last_fetch_at_jst": status["beds24_last_fetch_at_jst"],
                "revenue_data_status": rr0.get("revenue_data_status"),
                "same_month_revenue_comparison_applicable":
                    rr0.get("same_month_revenue_comparison_applicable", False),
                "revenue_comparison_status": rr0.get("revenue_comparison_status", "同月比較対象外"),
            })

    if own:
        conn.close()
    status["dry_run"] = dry_run
    return status
