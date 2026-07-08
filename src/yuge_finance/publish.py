"""BI公開（Cloudflare Pages用 静的ディレクトリへ反映）。

data/output/latest/bi/ のBIファイルを cloudflare/bi-web/public/data/ へ
JSON構文チェック → 一時ファイル → atomic replace でコピーし、manifestを作る。
既存公開ファイルを壊さない。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List

from . import config
from .bi_refresh import jst_str

PUBLISH_FILES = [
    "bi_snapshot.json", "bi_daily_timeseries.csv", "bi_monthly_kpi.csv",
    "bi_validation_status.json", "bi_exception_summary.json",
    "bank_cashflow_summary.json", "bank_cost_model_candidates.json",
    "fixed_variable_model_update_candidates.json",
]


class PublishError(RuntimeError):
    pass


def web_data_dir() -> Path:
    return config.ROOT / "cloudflare" / "bi-web" / "public" / "data"


def latest_bi_dir() -> Path:
    return config.DATA_DIR / "output" / "latest" / "bi"


def publish(latest_dir: Path = None, dst: Path = None) -> Dict:
    latest = latest_dir or latest_bi_dir()
    dst = dst or web_data_dir()

    present = [f for f in PUBLISH_FILES if (latest / f).exists()]
    if not present:
        raise PublishError(
            f"公開対象BIがありません: {latest}  先に refresh-beds24-bi を実行してください。")

    # JSON構文チェック（壊れていたら何も公開しない）
    for f in present:
        if f.endswith(".json"):
            try:
                json.loads((latest / f).read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                raise PublishError(f"JSON不正のため公開中止: {f} ({e})")

    dst.mkdir(parents=True, exist_ok=True)
    files_meta: List[Dict] = []
    for f in present:
        data = (latest / f).read_bytes()
        tmp = dst / (f + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dst / f)   # atomic
        files_meta.append({"name": f, "bytes": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()})

    snap = json.loads((dst / "bi_snapshot.json").read_text(encoding="utf-8"))
    status_path = latest / "bi_refresh_status.json"
    status = {}
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            status = {}

    combined = hashlib.sha256(
        "".join(m["sha256"] for m in files_meta).encode()).hexdigest()
    manifest = {
        "generated_at_jst": jst_str(),
        "source_months": status.get("source_months"),
        "beds24_last_fetch_at_jst": status.get("beds24_last_fetch_at_jst"),
        "revenue_data_status": snap.get("revenue_data_status"),
        "same_month_revenue_comparison_applicable":
            snap.get("same_month_revenue_comparison_applicable", False),
        "revenue_comparison_status": snap.get("revenue_comparison_status", "同月比較対象外"),
        "files": files_meta,
        "checksum": combined,
    }
    tmp = dst / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(dst / "manifest.json")

    return {"published": len(files_meta), "dst": str(dst), "checksum": combined}
