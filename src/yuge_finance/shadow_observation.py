"""Shadow-mode semantic false-negative observation (Beds24 webhook planner).

Context: the BiRefreshCoordinator webhook planner decides, from Beds24
booking-webhook events alone, whether a refresh is "warranted". Beds24's
Booking Webhook does not fire for every field change that matters to BI or
Staff Ops (e.g. some guest-detail edits) — a cycle where the planner would
have skipped (`reason == "shadow_unconditional"`) but the unconditional
shadow refresh actually produced a business-meaningful change is a *silent
false negative*: proof the planner-only signal is not yet sufficient for
active mode.

This module only ever answers "did the meaningful content change?" as one
of a small set of opaque status strings — "changed" / "unchanged" /
"no_baseline" / "error". The compared snapshots themselves (BI figures,
and for Staff Ops, guest name/phone/address/comments) are read into
process memory only for the duration of the comparison and are never
returned, logged, or written anywhere by this module. Callers (the CLI
commands below) must not print snapshot content either — only the status
string and counts.
"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from . import publish_r2
from .ops import build as ops_build

PUBLIC_API_BASE = "https://kiraku-bi.s-sato-dce.workers.dev"
STAFF_OPS_BUCKET = "kiraku-staff-ops-data"
STAFF_OPS_R2_KEY = "latest/staff_ops_snapshot.json"

STATUS_CHANGED = "changed"
STATUS_UNCHANGED = "unchanged"
STATUS_NO_BASELINE = "no_baseline"
STATUS_ERROR = "error"
VALID_STATUSES = (STATUS_CHANGED, STATUS_UNCHANGED, STATUS_NO_BASELINE, STATUS_ERROR)

# Fields that change purely from time/date progression, never from a real
# booking or business-data change, and would otherwise make every 15-minute
# comparison report "changed" even when nothing meaningful happened. Matched
# by substring/regex against each dict key at any nesting depth. A JST
# day-rollover is already reported separately by the Coordinator planner as
# reason="jst_date_rollover" (not "shadow_unconditional"), so excluding
# calendar-position fields here does not hide a genuinely new day — it only
# removes noise from the shadow_unconditional comparisons this module cares
# about.
_VOLATILE_KEY_RE = re.compile(r"(generated_at|calculated_at|fetch_at)")
_VOLATILE_EXACT_KEYS = frozenset({
    "current_date_jst",
    "today_jst",
    "today_global_jst",
    "yesterday_global_jst",
    "day_of_month",
    "days_elapsed_in_month",
    "days_remaining_in_month",
    "month_elapsed_rate",
    "month_remaining_rate",
    # publish_r2._apply_bank_sticky_fields() stamps this on every single
    # run that borrows bank_* fields from the previous published snapshot
    # (GitHub Actions never has the local bank CSV), even when the
    # effective bank data it copied is byte-for-byte identical to last
    # time. Without excluding it, the finalized-payload comparison (blocker
    # 2) would report "changed" on every 15-minute cycle regardless of any
    # real bank/business-data change.
    "bank_fields_preserved_at_jst",
})


def _is_volatile_key(key: str) -> bool:
    if key in _VOLATILE_EXACT_KEYS:
        return True
    return bool(_VOLATILE_KEY_RE.search(key))


def canonicalize(value: Any) -> Any:
    """Recursively strip volatile (time-progression-only) fields.

    Dict key order never affects the result (Python dict equality already
    ignores insertion order); this function's only job is removing fields
    that would otherwise cause a "changed" verdict on every single
    15-minute cycle regardless of any real business-data change.
    """
    if isinstance(value, dict):
        return {k: canonicalize(v) for k, v in value.items() if not _is_volatile_key(k)}
    if isinstance(value, list):
        return [canonicalize(v) for v in value]
    return value


def compare_snapshots(old: Optional[dict], new: dict) -> str:
    """Returns one of STATUS_NO_BASELINE / STATUS_UNCHANGED / STATUS_CHANGED.

    Never raises for well-formed dict inputs; callers are still expected to
    wrap calls to this in a try/except mapping any unexpected exception to
    STATUS_ERROR (see observe_bi / observe_staff_ops below), since `new` may
    come from a freshly-generated file this process does not fully control.
    """
    if old is None:
        return STATUS_NO_BASELINE
    return STATUS_CHANGED if canonicalize(old) != canonicalize(new) else STATUS_UNCHANGED


# ---------------------------------------------------------------- BI semantic bundle
#
# Active mode suppresses the ENTIRE refresh/publish cycle, which updates
# every available month's bi_snapshot.json plus the daily timeseries/
# monthly KPI/validation/exception outputs — not just the default month's
# snapshot. Comparing only /api/snapshot (as an earlier revision of this
# module did) could miss a webhook-silent change confined to a non-default
# month, or a within-month date move that changes bi_daily_timeseries.csv
# without changing the monthly aggregate (independent review blocker 3).
#
# A "BI semantic bundle" is therefore every file the production publish
# actually serves: the root-level files (publish_r2.UPLOAD_FILES) plus,
# for every month in manifest.available_months, the per-month files
# (publish_r2.MONTH_UPLOAD_FILENAMES). Deriving the file list from
# publish_r2's own constants — rather than a second hardcoded list here —
# means a future publish target can never silently fall out of observation
# just because this module wasn't updated too.
#
# publish_r2.OPTIONAL_UPLOAD_FILES (bank_cashflow_summary.json etc.) are
# excluded on purpose: the public Worker has no read route for them today
# (they are model-candidate files, not anything the frontend/dashboards
# serve), so including them would require a Worker deploy-surface change
# out of this PR's scope. If they ever get a public route, add them to
# publish_r2.UPLOAD_FILES and they will automatically join the bundle.
#
# Bundle shape: {"root": {filename: raw_text}, "months": {month: {filename:
# raw_text}}}. Values are kept as raw text (not pre-parsed) so capture_bi_
# baseline() and load_current_bi_bundle() stay symmetric regardless of
# file type; canonicalization/parsing happens once, at compare time.

def _fetch_public_text(path: str, timeout: int) -> Optional[str]:
    """Fetches one file from the public production BI Worker's /data/...
    routes. Returns raw text, or None on any failure."""
    try:
        resp = requests.get(f"{PUBLIC_API_BASE}{path}", timeout=timeout)
        if resp.status_code != 200:
            return None
        return resp.text
    except requests.RequestException:
        return None


def capture_bi_baseline(timeout: int = 20) -> Optional[dict]:
    """Fetches the full production BI semantic bundle (see module comment
    above) to serve as the "before this refresh" baseline. Returns None on
    ANY failure (including a single missing file) — a missing/partial
    baseline is reported as STATUS_NO_BASELINE, never STATUS_ERROR, and
    never blocks the refresh pipeline."""
    manifest_text = _fetch_public_text("/data/manifest.json", timeout)
    if manifest_text is None:
        return None
    try:
        manifest = json.loads(manifest_text)
    except ValueError:
        return None

    root: Dict[str, str] = {"manifest.json": manifest_text}
    for filename in publish_r2.UPLOAD_FILES:
        if filename == "manifest.json":
            continue
        text = _fetch_public_text(f"/data/{filename}", timeout)
        if text is None:
            return None
        root[filename] = text

    months: Dict[str, Dict[str, str]] = {}
    for month in manifest.get("available_months") or []:
        month_files: Dict[str, str] = {}
        for filename in publish_r2.MONTH_UPLOAD_FILENAMES:
            text = _fetch_public_text(f"/data/months/{month}/{filename}", timeout)
            if text is None:
                return None
            month_files[filename] = text
        months[month] = month_files

    return {"root": root, "months": months}


def load_current_bi_bundle(source_dir: Optional[Path] = None) -> dict:
    """Loads the full local BI semantic bundle (same file list as
    capture_bi_baseline()) from the publish source directory.

    Must be read AFTER publish_r2.publish(..., preserve_bank_fields_from_r2=
    True) has run, not before: that call mutates these same local
    bi_snapshot.json files into their finalized sticky-bank-merged form
    before uploading them. Reading them before publish would compare a
    pre-publish intermediate that production never actually serves
    (independent review blocker 2) — see refresh-bi-r2.yml's step order.
    """
    source_dir = source_dir or publish_r2.default_source_dir()
    manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))

    root: Dict[str, str] = {}
    for filename in publish_r2.UPLOAD_FILES:
        root[filename] = (source_dir / filename).read_text(encoding="utf-8")

    months: Dict[str, Dict[str, str]] = {}
    for month in manifest.get("available_months") or []:
        month_dir = source_dir / "months" / month
        month_files: Dict[str, str] = {}
        for filename in publish_r2.MONTH_UPLOAD_FILENAMES:
            path = month_dir / filename
            if path.exists():
                month_files[filename] = path.read_text(encoding="utf-8")
        months[month] = month_files

    return {"root": root, "months": months}


def _canonicalize_bundle_file(filename: str, text: str) -> Any:
    """Parses one bundle file's raw text into a comparable, canonicalized
    structure.

    .json files: parsed then volatile-key-stripped via canonicalize().
    .csv files: parsed into a list of row dicts via csv.DictReader, which
    normalizes whitespace/line-ending differences without reordering rows.
    Row order in bi_daily_timeseries.csv is calendar order, and calendar
    order is itself semantic — a booking moving from one date to another
    changes that row's values, not which row comes first — so preserving
    order (rather than sorting) is the correct behavior here, per the
    "don't change order if order is semantic" rule.
    """
    if filename.endswith(".csv"):
        return [dict(row) for row in csv.DictReader(io.StringIO(text))]
    return canonicalize(json.loads(text))


def _canonicalize_bi_bundle(bundle: dict) -> dict:
    return {
        "root": {fn: _canonicalize_bundle_file(fn, text) for fn, text in bundle["root"].items()},
        "months": {
            month: {fn: _canonicalize_bundle_file(fn, text) for fn, text in files.items()}
            for month, files in bundle["months"].items()
        },
    }


def compare_bi_bundle(old: Optional[dict], new: dict) -> str:
    """Returns one of STATUS_NO_BASELINE / STATUS_UNCHANGED / STATUS_CHANGED
    for two full BI semantic bundles. A month present in one bundle but not
    the other (newly available, or no longer available) counts as changed,
    same as any other structural difference — dict equality on the "months"
    key already catches that with no special-casing needed."""
    if old is None:
        return STATUS_NO_BASELINE
    return STATUS_CHANGED if _canonicalize_bi_bundle(old) != _canonicalize_bi_bundle(new) else STATUS_UNCHANGED


def observe_bi(old: Optional[dict], source_dir: Optional[Path] = None) -> str:
    try:
        new = load_current_bi_bundle(source_dir)
        return compare_bi_bundle(old, new)
    except Exception:
        return STATUS_ERROR


# ---------------------------------------------------------------- Staff Ops baseline capture

def _wrangler_cmd() -> list:
    return ["npx", "wrangler"] if shutil.which("wrangler") is None else ["wrangler"]


def capture_staff_ops_baseline(timeout: int = 60) -> Optional[dict]:
    """Fetches the currently-published Staff Ops snapshot from the private
    R2 bucket (kiraku-staff-ops-data), to serve as the "before this run"
    baseline. Requires Cloudflare credentials in the environment (same ones
    the workflow already uses for R2 publish) and `wrangler` on PATH.
    Returns None on any failure (bucket/object not yet existing on first
    run, transient error, credentials not usable here, etc.) — reported as
    STATUS_NO_BASELINE, never blocks the pipeline.

    The fetched object is parsed in-process and returned as a dict; it may
    contain guest PII (name/phone/address/comments) which callers must
    never log, persist, or otherwise expose beyond feeding it into
    compare_snapshots().
    """
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "staff_ops_baseline.json"
            cmd = _wrangler_cmd() + [
                "r2", "object", "get", f"{STAFF_OPS_BUCKET}/{STAFF_OPS_R2_KEY}",
                "--file", str(out_path), "--remote",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode != 0 or not out_path.exists():
                return None
            return json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def load_current_staff_ops_snapshot(path: Optional[Path] = None) -> dict:
    path = path or ops_build.default_out_path()
    return json.loads(Path(path).read_text(encoding="utf-8"))


def observe_staff_ops(old: Optional[dict], path: Optional[Path] = None) -> str:
    try:
        new = load_current_staff_ops_snapshot(path)
        return compare_snapshots(old, new)
    except Exception:
        return STATUS_ERROR
