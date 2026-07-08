"""Beds24速報BIの定期巡回更新（喜らく単体）。

15分おきに想定。Beds24取得とBIファイル再生成（人件費予測・損益分岐点を含む）のみ行う。
仕訳生成・PL/BS/CF確定・Excel出力・銀行/現金/手動取込・close-month は一切行わない。
APIエラー時は既存BIファイルを壊さない。
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import config, csvio, db, monthly
from .accounting import reconciliation
from .normalize.schema import BookingRecord
from .reports import bi_export, breakeven_report, labor_report

JST = timezone(timedelta(hours=9))
BI_FILES = ["bi_snapshot.json", "bi_daily_timeseries.csv", "bi_monthly_kpi.csv",
            "bi_validation_status.json", "bi_exception_summary.json",
            "bank_cashflow_summary.json", "bank_cost_model_candidates.json",
            "fixed_variable_model_update_candidates.json"]

# 対象月自動抽出（--auto-months-with-bookings）の追加取得ウィンドウ。
# DB既存データのスキャンには影響しない（あくまで新規fetchの探索範囲）。
AUTO_LOOKBACK_MONTHS = 2
AUTO_LOOKAHEAD_MONTHS = 4

MONTH_SNAPSHOT_FILES = {
    "snapshot": "bi_snapshot.json",
    "daily_timeseries": "bi_daily_timeseries.csv",
    "monthly_kpi": "bi_monthly_kpi.csv",
    "validation": "bi_validation_status.json",
    "exceptions": "bi_exception_summary.json",
}


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


def _month_add(month: str, n: int) -> str:
    y, m = (int(x) for x in month.split("-"))
    mm = m + n
    yy = y + (mm - 1) // 12
    mm = (mm - 1) % 12 + 1
    return f"{yy:04d}-{mm:02d}"


def _booking_touched_months(booking: BookingRecord) -> set:
    """宿泊日ベース（チェックイン〜チェックアウト前日まで）でbookingがかかる年月集合を返す。"""
    ci, co = booking.checkin_date, booking.checkout_date
    if not ci:
        return set()
    if not co or co <= ci:
        return {ci[:7]}
    months = set()
    d = datetime.strptime(ci[:10], "%Y-%m-%d").date()
    end = datetime.strptime(co[:10], "%Y-%m-%d").date()
    while d < end:
        months.add(d.strftime("%Y-%m"))
        d += timedelta(days=1)
    return months


def _scan_months_from_db(conn, exclude_statuses: List[str]) -> Dict[str, List[str]]:
    """DBに既にあるbeds24_bookings全件から、宿泊日ベースの対象月集合を計算する（追加fetchはしない）。"""
    all_bookings = db.load_objects(conn, "beds24_bookings")
    for b in all_bookings:
        b.finalize()
    any_months, active_months = set(), set()
    for b in all_bookings:
        touched = _booking_touched_months(b)
        any_months |= touched
        if not b.is_cancelled(exclude_statuses):
            active_months |= touched
    return {
        "months_with_any_booking": sorted(any_months),
        "months_with_active_booking": sorted(active_months),
    }


def discover_months_with_bookings(conn, exclude_statuses: List[str], current: str = None) -> Dict:
    """--auto-months-with-bookings 用。広めの窓でBeds24を追加取得してからDB全体をスキャンする。"""
    current = current or current_month()
    window = month_list(_month_add(current, -AUTO_LOOKBACK_MONTHS),
                        AUTO_LOOKBACK_MONTHS + AUTO_LOOKAHEAD_MONTHS + 1)
    for m in window:
        try:
            _fetch_beds24(m, conn)
        except Exception:  # noqa: BLE001 - 取得失敗月はスキップし既存DBデータで継続
            continue

    found = _scan_months_from_db(conn, exclude_statuses)
    # 窓の外で発見された月（当初想定より広く予約がある場合）も確定させるため追加fetch
    extra = ((set(found["months_with_any_booking"]) | set(found["months_with_active_booking"]))
            - set(window))
    if extra:
        for m in sorted(extra):
            try:
                _fetch_beds24(m, conn)
            except Exception:  # noqa: BLE001
                continue
        found = _scan_months_from_db(conn, exclude_statuses)
    return found


def compute_default_month(available_months: List[str], current: str) -> Optional[str]:
    """1.現在月に予約があれば現在月 2.無ければ最も近い予約月(同点は未来優先) 3.それも無ければ最新月。"""
    if not available_months:
        return None
    if current in available_months:
        return current

    def idx(m):
        y, mo = (int(x) for x in m.split("-"))
        return y * 12 + mo

    ci = idx(current)
    return min(available_months, key=lambda m: (abs(idx(m) - ci), 0 if idx(m) >= ci else 1))


def _month_snapshot_paths(month: str) -> Dict[str, str]:
    base = f"latest/months/{month}"
    return {k: f"{base}/{fn}" for k, fn in MONTH_SNAPSHOT_FILES.items()}


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


def refresh(months: List[str], dry_run: bool = False, conn=None,
           auto_months_with_bookings: bool = False, today_jst_override: str = None) -> Dict:
    """today_jst_override: --today-jst検証用のYYYY-MM-DD文字列。本番launchdでは指定しない
    （未指定時は実行のたびにJST今日を再計算する）。
    """
    own = conn is None
    conn = conn or db.connect()
    started = jst_str()
    success: List[str] = []
    errors: List[Dict] = []
    month_ctxs: List[Dict] = []

    exclude = config.kiraku().get("revenue", {}).get(
        "exclude_statuses", ["cancelled", "canceled", "black"])
    today_jst = date.fromisoformat(today_jst_override) if today_jst_override else None

    if auto_months_with_bookings:
        discovered = discover_months_with_bookings(conn, exclude)
        if discovered["months_with_any_booking"]:
            months = discovered["months_with_any_booking"]

    for m in months:
        try:
            n = _fetch_beds24(m, conn)
        except Exception as e:  # noqa: BLE001 - 既存BIを壊さず継続
            errors.append({"month": m, "stage": "fetch", "error": str(e)})
            continue
        ctx = monthly.assemble(m, conn, today_jst=today_jst)  # 読み取り計算のみ（永続化しない）
        checks = reconciliation.run(m, ctx)      # workbook_path無し→Excelチェック無し
        sev = reconciliation.severity(checks)
        if not dry_run:
            out_dir = config.DATA_DIR / "output" / m
            bi_export.write_all(m, ctx, checks, [], sev, out_dir, conn=conn)
            # build-labor-forecast / build-breakeven 相当（速報。仕訳/Excelは書かない）
            labor_report.write(m, ctx["labor_forecast"], out_dir)
            breakeven_report.write(m, ctx["breakeven_model"], ctx["bookings"], exclude, out_dir,
                                   pace=ctx.get("pace_model"))
            # 月別ディレクトリ単体でも bi_monthly_kpi.csv を持たせる（latest/months/{m}/ コピー用）
            bi_export.write_monthly_kpi([ctx], out_dir / "bi" / "bi_monthly_kpi.csv")
        month_ctxs.append(ctx)
        success.append(m)

    # 予約が実在する月一覧（DB全体スキャン。追加fetchはしない＝legacyパスでも無償で取得できる）
    booking_months = _scan_months_from_db(conn, exclude)
    available_months = [m for m in booking_months["months_with_any_booking"] if m in success] or list(success)
    default_month = compute_default_month(available_months, current_month())
    if default_month in success:
        cur = default_month
    elif success:
        cur = success[0]
    else:
        cur = months[0] if months else None

    # current/latest は表示対象月(cur)の取得成功時のみ更新（失敗時は既存維持）
    if not dry_run and cur in success:
        for name in ("current", "latest"):
            _copy_bi(_bi_dir(cur), _bi_dir(name))
            bi_export.write_monthly_kpi(month_ctxs, _bi_dir(name) / "bi_monthly_kpi.csv")
            for m in success:
                _copy_bi(_bi_dir(m), _bi_dir(name) / "months" / m)

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
        "default_month": default_month,
        "available_months": available_months,
        "months_with_any_booking": booking_months["months_with_any_booking"],
        "months_with_active_booking": booking_months["months_with_active_booking"],
    }
    if not dry_run and cur in success:
        cur_ctx = next((c for c in month_ctxs if c["month"] == cur),
                       month_ctxs[0] if month_ctxs else {})
        rr_cur = cur_ctx.get("revenue_recon", {})
        month_snapshots = {m: _month_snapshot_paths(m) for m in available_months}
        today_new_by_month = {
            c["month"]: {
                "count": c.get("today_new_bookings", {}).get("today_new_booking_count"),
                "revenue": c.get("today_new_bookings", {}).get("today_new_booking_revenue"),
            }
            for c in month_ctxs if c["month"] in available_months
        }
        today_new_summary = {
            "calculated_at_jst": jst_str(),
            "date_jst": jst_now().date().isoformat(),
            "by_month": today_new_by_month,
        }
        for name in ("current", "latest"):
            _atomic_write_json(_bi_dir(name) / "bi_refresh_status.json", status)
            # publish-bi-r2 が data/output/latest/bi/manifest.json をそのまま読めるように、
            # ここでも簡易manifestを生成しておく（Cloudflare公開用の詳細manifestはpublish.publish()側）。
            _atomic_write_json(_bi_dir(name) / "manifest.json", {
                "generated_at_jst": jst_str(),
                "source_months": months,
                "beds24_last_fetch_at_jst": status["beds24_last_fetch_at_jst"],
                "revenue_data_status": rr_cur.get("revenue_data_status"),
                "same_month_revenue_comparison_applicable":
                    rr_cur.get("same_month_revenue_comparison_applicable", False),
                "revenue_comparison_status": rr_cur.get("revenue_comparison_status", "同月比較対象外"),
                "default_month": default_month,
                "available_months": available_months,
                "months_with_any_booking": booking_months["months_with_any_booking"],
                "months_with_active_booking": booking_months["months_with_active_booking"],
                "month_snapshots": month_snapshots,
                "today_new_booking_summary": today_new_summary,
            })

    if own:
        conn.close()
    status["dry_run"] = dry_run
    return status
