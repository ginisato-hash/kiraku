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
from typing import Dict, List

from . import config

UPLOAD_FILES = [
    "manifest.json", "bi_snapshot.json", "bi_daily_timeseries.csv",
    "bi_monthly_kpi.csv", "bi_validation_status.json", "bi_exception_summary.json",
]

# 銀行口座実績レイヤー由来（本機能導入前に生成されたBI出力には無い場合があるため任意扱い）。
OPTIONAL_UPLOAD_FILES = [
    "bank_cashflow_summary.json", "bank_cost_model_candidates.json",
    "fixed_variable_model_update_candidates.json",
]

REQUIRED_SNAPSHOT_FIELDS = [
    "cash_operating_breakeven_revenue",
    "cash_operating_breakeven_achievement_rate",
    "booking_pace_status",
    "month_elapsed_rate",
    "projected_month_end_bep_achievement_rate",
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

    return issues


def _wrangler_available() -> bool:
    return shutil.which("npx") is not None or shutil.which("wrangler") is not None


def _put(bucket: str, prefix: str, filename: str, file_path: Path, cwd: Path) -> Dict:
    key = f"{prefix}/{filename}"
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
           worker_dir: Path = None) -> Dict:
    source_dir = source_dir or default_source_dir()
    worker_dir = worker_dir or (config.ROOT / "cloudflare" / "bi-web")

    issues = validate(source_dir)
    if issues:
        raise PublishR2Error("アップロード前検証に失敗:\n  - " + "\n  - ".join(issues))

    snapshot = json.loads((source_dir / "bi_snapshot.json").read_text(encoding="utf-8"))
    generated_at = snapshot.get("generated_at_jst") or snapshot.get("current_date_jst")

    target_files = UPLOAD_FILES + [fn for fn in OPTIONAL_UPLOAD_FILES if (source_dir / fn).exists()]

    if dry_run:
        return {
            "dry_run": True, "bucket": bucket, "prefix": prefix,
            "uploaded_count": 0,
            "would_upload_keys": [f"{prefix}/{fn}" for fn in target_files],
            "generated_at_jst": generated_at,
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
    }
