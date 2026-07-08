"""BI公開（Cloudflare R2への直接アップロード。Worker本体はdeployしない）。

data/output/latest/bi/ の6ファイルを R2 bucket kiraku-bi-data の latest/ へ
`wrangler r2 object put` をsubprocessで実行してアップロードする。
Cloudflare認証はwrangler login/API tokenに委ねる（Pythonから直接APIを叩かない）。
アップロード前にJSON検証を行い、失敗時はローカルBI・既存R2データを壊さない
（object putは対象keyのみ上書きするため、失敗時も他keyは無影響）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import requests

from . import config
from .reports import bank_sticky_fields

# 銀行CF summary sticky field引き継ぎ用の公開API(Worker)。R2 credentialのpublish処理と
# 同じタイミングで、直近公開済みsnapshotを読むために使う（rawのbank明細ではなく
# 集計済みsnapshotフィールドのみを対象とする）。
PUBLIC_API_BASE = "https://kiraku-bi.s-sato-dce.workers.dev"

UPLOAD_FILES = [
    "manifest.json", "bi_snapshot.json", "bi_daily_timeseries.csv",
    "bi_monthly_kpi.csv", "bi_validation_status.json", "bi_exception_summary.json",
]

# 銀行口座実績レイヤー由来（本機能導入前に生成されたBI出力には無い場合があるため任意扱い）。
OPTIONAL_UPLOAD_FILES = [
    "bank_cashflow_summary.json", "bank_cost_model_candidates.json",
    "fixed_variable_model_update_candidates.json",
]

# 月別ディレクトリ(months/{YYYY-MM}/)配下のアップロード対象ファイル名。
MONTH_UPLOAD_FILENAMES = [
    "bi_snapshot.json", "bi_daily_timeseries.csv", "bi_monthly_kpi.csv",
    "bi_validation_status.json", "bi_exception_summary.json",
]

REQUIRED_SNAPSHOT_FIELDS = [
    "cash_operating_breakeven_revenue",
    "cash_operating_breakeven_achievement_rate",
    "booking_pace_status",
    "month_elapsed_rate",
    "projected_month_end_bep_achievement_rate",
]

# 月別snapshot(months/{YYYY-MM}/bi_snapshot.json)に必須のフィールド。
REQUIRED_MONTH_SNAPSHOT_FIELDS = [
    "target_month", "beds24_revenue_net_for_bi",
    "cash_operating_breakeven_revenue", "booking_pace_status",
]

DEFAULT_BUCKET = "kiraku-bi-data"
DEFAULT_PREFIX = "latest"


class PublishR2Error(RuntimeError):
    pass


def default_source_dir() -> Path:
    return config.DATA_DIR / "output" / "latest" / "bi"


def validate(source_dir: Path) -> List[str]:
    """アップロード前検証。問題があれば理由のリストを返す（空=問題なし）。"""
    issues: List[str] = []
    manifest_path = source_dir / "manifest.json"
    snapshot_path = source_dir / "bi_snapshot.json"

    if not manifest_path.exists():
        issues.append(f"manifest.json が存在しません: {manifest_path}")
    if not snapshot_path.exists():
        issues.append(f"bi_snapshot.json が存在しません: {snapshot_path}")
    if issues:
        return issues  # 以降の検証はファイルが無いと実行できない

    for fn in UPLOAD_FILES:
        p = source_dir / fn
        if not p.exists():
            issues.append(f"{fn} が存在しません: {p}")
            continue
        if fn.endswith(".json"):
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                issues.append(f"{fn} のJSON解析に失敗: {e}")

    for fn in OPTIONAL_UPLOAD_FILES:
        p = source_dir / fn
        if p.exists():
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                issues.append(f"{fn} のJSON解析に失敗: {e}")

    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        issues.append(f"bi_snapshot.json のJSON解析に失敗: {e}")
        return issues

    if not snapshot.get("generated_at_jst") and not snapshot.get("current_date_jst"):
        issues.append("bi_snapshot.json に generated_at_jst / current_date_jst がありません")
    for field in REQUIRED_SNAPSHOT_FIELDS:
        if field not in snapshot:
            issues.append(f"bi_snapshot.json に必須フィールドがありません: {field}")

    issues.extend(_validate_month_snapshots(source_dir, manifest_path))
    return issues


def _validate_month_snapshots(source_dir: Path, manifest_path: Path) -> List[str]:
    """月別ディレクトリ(months/{YYYY-MM}/)の検証。manifestにavailable_monthsが無ければ検証をスキップする
    （本機能導入前に生成されたBI出力との後方互換のため）。
    """
    issues: List[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return [f"manifest.json のJSON解析に失敗: {e}"]

    if "available_months" not in manifest:
        return issues  # 月別対応前のBI出力。従来通りlatest/*のみを対象とする。
    available_months = manifest.get("available_months") or []
    if not isinstance(available_months, list):
        return ["manifest.json の available_months はlistである必要があります"]

    for month in available_months:
        month_dir = source_dir / "months" / month
        snap_path = month_dir / "bi_snapshot.json"
        if not snap_path.exists():
            issues.append(f"months/{month}/bi_snapshot.json が存在しません: {snap_path}")
            continue
        try:
            month_snap = json.loads(snap_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            issues.append(f"months/{month}/bi_snapshot.json のJSON解析に失敗: {e}")
            continue
        for field in REQUIRED_MONTH_SNAPSHOT_FIELDS:
            if field not in month_snap:
                issues.append(f"months/{month}/bi_snapshot.json に必須フィールドがありません: {field}")
    return issues


def _month_upload_targets(source_dir: Path, manifest: Dict) -> List[str]:
    """アップロード対象の月別ファイルの相対key一覧(例: months/2026-07/bi_snapshot.json)を返す。"""
    targets: List[str] = []
    for month in manifest.get("available_months") or []:
        month_dir = source_dir / "months" / month
        for fn in MONTH_UPLOAD_FILENAMES:
            if (month_dir / fn).exists():
                targets.append(f"months/{month}/{fn}")
    return targets


def _fetch_public_snapshot(month: Optional[str] = None, timeout: int = 20) -> Optional[Dict]:
    """公開Worker APIから直近snapshotを取得する。失敗時はNoneを返す(publishを止めない)。"""
    url = f"{PUBLIC_API_BASE}/api/snapshot"
    if month:
        url += f"?month={month}"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code != 200:
            return None
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def _apply_bank_sticky_fields(source_dir: Path, manifest: Dict) -> Dict[str, int]:
    """root/月別snapshotのbank_*フィールドについて、今回値が無効な場合のみ直近公開snapshotから
    引き継ぎ、ローカルファイルを書き換える。bank_fields_sourceの内訳件数を返す。
    """
    counts = {"current_import": 0, "previous_r2_snapshot": 0, "not_available": 0}

    root_path = source_dir / "bi_snapshot.json"
    root_snapshot = json.loads(root_path.read_text(encoding="utf-8"))
    previous_root = _fetch_public_snapshot()
    merged_root = bank_sticky_fields.merge_sticky_bank_fields(root_snapshot, previous_root)
    root_path.write_text(
        json.dumps(merged_root, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    counts[merged_root["bank_fields_source"]] += 1

    for month in manifest.get("available_months") or []:
        month_path = source_dir / "months" / month / "bi_snapshot.json"
        if not month_path.exists():
            continue
        month_snapshot = json.loads(month_path.read_text(encoding="utf-8"))
        previous_month = _fetch_public_snapshot(month)
        if previous_month is None:
            # 月別previousが取得できない場合はdefault previous snapshotから引き継ぐ。
            previous_month = previous_root
        merged_month = bank_sticky_fields.merge_sticky_bank_fields(month_snapshot, previous_month)
        month_path.write_text(
            json.dumps(merged_month, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        counts[merged_month["bank_fields_source"]] += 1

    return counts


def _wrangler_available() -> bool:
    return shutil.which("npx") is not None or shutil.which("wrangler") is not None


def _put(bucket: str, prefix: str, relative_key: str, file_path: Path, cwd: Path) -> Dict:
    key = f"{prefix}/{relative_key}"
    cmd = ["npx", "wrangler", "r2", "object", "put", f"{bucket}/{key}",
           "--file", str(file_path), "--remote"]
    try:
        result = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return {"key": key, "ok": False, "error": str(e)}
    if result.returncode != 0:
        return {"key": key, "ok": False, "error": (result.stderr or result.stdout).strip()[:500]}
    return {"key": key, "ok": True}


def publish(source_dir: Path = None, bucket: str = DEFAULT_BUCKET,
           prefix: str = DEFAULT_PREFIX, dry_run: bool = False,
           worker_dir: Path = None, preserve_bank_fields_from_r2: bool = False) -> Dict:
    source_dir = source_dir or default_source_dir()
    worker_dir = worker_dir or (config.ROOT / "cloudflare" / "bi-web")

    issues = validate(source_dir)
    if issues:
        raise PublishR2Error("アップロード前検証に失敗:\n  - " + "\n  - ".join(issues))

    manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))

    # dry-runはローカルファイルを書き換えず、ネットワークアクセスもしない（一覧表示のみ）。
    bank_fields_sources = None
    if preserve_bank_fields_from_r2 and not dry_run:
        bank_fields_sources = _apply_bank_sticky_fields(source_dir, manifest)

    snapshot = json.loads((source_dir / "bi_snapshot.json").read_text(encoding="utf-8"))
    generated_at = snapshot.get("generated_at_jst") or snapshot.get("current_date_jst")

    target_files = (UPLOAD_FILES
                   + [fn for fn in OPTIONAL_UPLOAD_FILES if (source_dir / fn).exists()]
                   + _month_upload_targets(source_dir, manifest))

    default_month = manifest.get("default_month")

    if dry_run:
        return {
            "dry_run": True, "bucket": bucket, "prefix": prefix,
            "uploaded_count": 0,
            "would_upload_keys": [f"{prefix}/{fn}" for fn in target_files],
            "generated_at_jst": generated_at,
            "default_month": default_month,
            "bank_fields_sources": bank_fields_sources,
        }

    if not _wrangler_available():
        raise PublishR2Error(
            "wrangler(npx)が見つかりません。cloudflare/bi-web で `npm install` を実行してください。")

    results = []
    for fn in target_files:
        res = _put(bucket, prefix, fn, source_dir / fn, worker_dir)
        results.append(res)
        if not res["ok"]:
            break  # 途中失敗しても既存R2データ・ローカルBIは壊さない（他keyは無影響のため停止でよい）

    failed = [r for r in results if not r["ok"]]
    succeeded = [r for r in results if r["ok"]]
    if failed:
        raise PublishR2Error(
            f"アップロード失敗: key={failed[0]['key']} error={failed[0]['error']}\n"
            f"成功済み: {[r['key'] for r in succeeded]}")

    return {
        "dry_run": False, "bucket": bucket, "prefix": prefix,
        "uploaded_count": len(succeeded),
        "uploaded_keys": [r["key"] for r in succeeded],
        "generated_at_jst": generated_at,
        "default_month": default_month,
        "bank_fields_sources": bank_fields_sources,
    }
