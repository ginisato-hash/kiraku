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

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

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


# ---------------------------------------------------------------- BI baseline capture

def capture_bi_baseline(timeout: int = 20) -> Optional[dict]:
    """Fetches the currently-public BI snapshot (default month) to serve as
    the "before this refresh" baseline. Returns None on any failure — a
    missing baseline is reported as STATUS_NO_BASELINE, never an error, and
    never blocks the refresh pipeline."""
    try:
        resp = requests.get(f"{PUBLIC_API_BASE}/api/snapshot", timeout=timeout)
        if resp.status_code != 200:
            return None
        return resp.json()
    except (requests.RequestException, ValueError):
        return None


def load_current_bi_snapshot(source_dir: Optional[Path] = None) -> dict:
    """Loads the just-generated default-month BI snapshot from the local
    publish source directory (same directory publish-bi-r2 reads from),
    matching whichever month the public /api/snapshot would currently
    serve (manifest.default_month), so the comparison is apples-to-apples
    with capture_bi_baseline()'s output."""
    source_dir = source_dir or publish_r2.default_source_dir()
    manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    default_month = manifest.get("default_month")
    if default_month:
        month_path = source_dir / "months" / default_month / "bi_snapshot.json"
        if month_path.exists():
            return json.loads(month_path.read_text(encoding="utf-8"))
    return json.loads((source_dir / "bi_snapshot.json").read_text(encoding="utf-8"))


def observe_bi(old: Optional[dict], source_dir: Optional[Path] = None) -> str:
    try:
        new = load_current_bi_snapshot(source_dir)
        return compare_snapshots(old, new)
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
