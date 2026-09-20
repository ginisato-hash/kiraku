"""shadow_observation.py — semantic canonicalizer + no-baseline/error handling
for the shadow-mode false-negative observer (KIRAKU BI PHASE 2).

PII guarantee under test: a change in a guest-PII-shaped field (name/phone/
address/comment) must still be detected as "changed" (Staff Ops semantic
comparison must not silently ignore it), but every function here only ever
returns one of the four opaque status strings — never the compared values
themselves. test_pii_value_never_leaks_into_return_value asserts this
directly.

BI comparisons are covered against the full "BI semantic bundle" (all
available months' bi_snapshot.json plus bi_daily_timeseries.csv/
bi_monthly_kpi.csv/bi_validation_status.json/bi_exception_summary.json —
see shadow_observation.capture_bi_baseline/load_current_bi_bundle), per
independent review blocker 3: a webhook-silent change confined to a
non-default month, or a within-month date move that only affects the daily
timeseries, must still be detected.
"""
import json
from pathlib import Path

from yuge_finance import publish_r2, shadow_observation as so
from yuge_finance.reports import bank_sticky_fields


# ---------------------------------------------------------------- canonicalize

def test_canonicalize_strips_generated_at_and_similar_timestamps():
    data = {
        "generated_at_jst": "2026-09-20T10:00:00+09:00",
        "beds24_last_fetch_at_jst": "2026-09-20T10:00:00+09:00",
        "calculated_at_jst": "2026-09-20T10:00:00+09:00",
        "revenue": 12345,
    }
    assert so.canonicalize(data) == {"revenue": 12345}


def test_canonicalize_strips_exact_calendar_position_keys():
    data = {
        "current_date_jst": "2026-09-20",
        "today_jst": "2026-09-20",
        "day_of_month": 20,
        "days_elapsed_in_month": 20,
        "days_remaining_in_month": 10,
        "month_elapsed_rate": 0.66,
        "month_remaining_rate": 0.34,
        "adr": 9000,
    }
    assert so.canonicalize(data) == {"adr": 9000}


def test_canonicalize_strips_bank_fields_preserved_at_jst():
    """publish_r2._apply_bank_sticky_fields() stamps this fresh on every
    run that borrows bank_* fields from the previous published snapshot,
    even when the effective bank data is identical — it must never by
    itself cause a "changed" verdict (independent review blocker 2)."""
    data = {"bank_fields_preserved_at_jst": "2026-09-20T10:00:00+09:00", "revenue": 100}
    assert so.canonicalize(data) == {"revenue": 100}


def test_canonicalize_recurses_into_nested_dicts_and_lists():
    data = {
        "breakeven": {"generated_at_jst": "x", "revenue": 100},
        "daily_global_summary": {
            "today_new_bookings": {"calculated_at_jst": "x", "count": 3},
        },
        "room_type_daily_occupancy": [
            {"generated_at_jst": "x", "room_type": "twin", "count": 2},
        ],
    }
    assert so.canonicalize(data) == {
        "breakeven": {"revenue": 100},
        "daily_global_summary": {"today_new_bookings": {"count": 3}},
        "room_type_daily_occupancy": [{"room_type": "twin", "count": 2}],
    }


def test_canonicalize_leaves_non_volatile_fields_untouched():
    data = {"guest_name": "山田太郎", "phone": "090-0000-0000", "status": "confirmed"}
    assert so.canonicalize(data) == data


# ---------------------------------------------------------------- compare_snapshots

def test_compare_no_baseline():
    assert so.compare_snapshots(None, {"revenue": 100}) == so.STATUS_NO_BASELINE


def test_compare_unchanged_when_only_volatile_fields_differ():
    old = {"generated_at_jst": "2026-09-20T10:00:00+09:00", "revenue": 100}
    new = {"generated_at_jst": "2026-09-20T10:15:00+09:00", "revenue": 100}
    assert so.compare_snapshots(old, new) == so.STATUS_UNCHANGED


def test_compare_changed_when_a_business_field_differs():
    old = {"generated_at_jst": "t1", "revenue": 100}
    new = {"generated_at_jst": "t2", "revenue": 150}
    assert so.compare_snapshots(old, new) == so.STATUS_CHANGED


def test_compare_unchanged_is_indifferent_to_dict_key_ordering():
    old = {"a": 1, "b": 2, "generated_at_jst": "t1"}
    new = {"generated_at_jst": "t2", "b": 2, "a": 1}
    assert so.compare_snapshots(old, new) == so.STATUS_UNCHANGED


def test_compare_changed_on_pii_field_content_change():
    old = {"guest_name": "山田太郎", "generated_at_jst": "t1"}
    new = {"guest_name": "佐藤花子", "generated_at_jst": "t2"}
    assert so.compare_snapshots(old, new) == so.STATUS_CHANGED


def test_pii_value_never_leaks_into_return_value():
    old = {"guest_name": "山田太郎", "phone": "090-1111-2222"}
    new = {"guest_name": "佐藤花子", "phone": "090-3333-4444"}
    result = so.compare_snapshots(old, new)
    assert result == so.STATUS_CHANGED
    # the only thing that ever leaves compare_snapshots is one of the four
    # opaque status words — assert none of the compared PII values appear.
    assert "山田" not in result and "佐藤" not in result and "090" not in result
    assert result in so.VALID_STATUSES


# ---------------------------------------------------------------- BI semantic bundle fixtures

def _write_bi_bundle_dir(base: Path, manifest: dict, root_overrides: dict = None,
                          months_overrides: dict = None) -> None:
    """Writes a minimal local BI publish directory (every publish_r2.
    UPLOAD_FILES root file + publish_r2.MONTH_UPLOAD_FILENAMES for every
    manifest.available_months entry), so load_current_bi_bundle() never
    raises FileNotFoundError on it. Callers override just the file(s) they
    care about for a given test; everything else is a harmless placeholder.
    """
    base.mkdir(parents=True, exist_ok=True)
    (base / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    root_overrides = root_overrides or {}
    for fn in publish_r2.UPLOAD_FILES:
        if fn == "manifest.json":
            continue
        default = "{}" if fn.endswith(".json") else ""
        (base / fn).write_text(root_overrides.get(fn, default), encoding="utf-8")
    months_overrides = months_overrides or {}
    for month in manifest.get("available_months") or []:
        month_dir = base / "months" / month
        month_dir.mkdir(parents=True, exist_ok=True)
        overrides = months_overrides.get(month, {})
        for fn in publish_r2.MONTH_UPLOAD_FILENAMES:
            default = "{}" if fn.endswith(".json") else ""
            (month_dir / fn).write_text(overrides.get(fn, default), encoding="utf-8")


# ---------------------------------------------------------------- observe_bi (full semantic bundle)

def test_observe_bi_returns_error_when_current_bundle_unreadable(tmp_path):
    # no manifest.json / any BI file present at all
    status = so.observe_bi(old={"root": {}, "months": {}}, source_dir=tmp_path)
    assert status == so.STATUS_ERROR


def test_observe_bi_no_baseline_when_old_is_none(tmp_path):
    _write_bi_bundle_dir(tmp_path, {"default_month": None, "available_months": []})
    assert so.observe_bi(old=None, source_dir=tmp_path) == so.STATUS_NO_BASELINE


def test_observe_bi_unchanged_when_bundle_is_identical(tmp_path):
    manifest = {"default_month": "2026-09", "available_months": ["2026-09", "2026-10"]}
    overrides = {
        "2026-09": {"bi_snapshot.json": json.dumps({"target_month": "2026-09", "revenue": 100})},
        "2026-10": {"bi_snapshot.json": json.dumps({"target_month": "2026-10", "revenue": 200})},
    }
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    _write_bi_bundle_dir(old_dir, manifest, months_overrides=overrides)
    _write_bi_bundle_dir(new_dir, manifest, months_overrides=overrides)
    old_bundle = so.load_current_bi_bundle(old_dir)
    assert so.observe_bi(old=old_bundle, source_dir=new_dir) == so.STATUS_UNCHANGED


def test_observe_bi_unchanged_when_only_volatile_fields_differ(tmp_path):
    manifest = {"default_month": "2026-09", "available_months": ["2026-09"]}
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    _write_bi_bundle_dir(old_dir, manifest,
        root_overrides={"bi_snapshot.json": json.dumps({"generated_at_jst": "t1", "revenue": 100})},
        months_overrides={"2026-09": {"bi_snapshot.json": json.dumps(
            {"generated_at_jst": "t1", "bank_fields_preserved_at_jst": "t1", "revenue": 100})}})
    _write_bi_bundle_dir(new_dir, manifest,
        root_overrides={"bi_snapshot.json": json.dumps({"generated_at_jst": "t2", "revenue": 100})},
        months_overrides={"2026-09": {"bi_snapshot.json": json.dumps(
            {"generated_at_jst": "t2", "bank_fields_preserved_at_jst": "t2", "revenue": 100})}})
    old_bundle = so.load_current_bi_bundle(old_dir)
    assert so.observe_bi(old=old_bundle, source_dir=new_dir) == so.STATUS_UNCHANGED


def test_observe_bi_unchanged_on_dict_key_ordering_difference(tmp_path):
    manifest = {"default_month": "2026-09", "available_months": []}
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    _write_bi_bundle_dir(old_dir, manifest, root_overrides={"bi_snapshot.json": json.dumps({"a": 1, "b": 2})})
    _write_bi_bundle_dir(new_dir, manifest, root_overrides={"bi_snapshot.json": json.dumps({"b": 2, "a": 1})})
    old_bundle = so.load_current_bi_bundle(old_dir)
    assert so.observe_bi(old=old_bundle, source_dir=new_dir) == so.STATUS_UNCHANGED


def test_observe_bi_unchanged_on_stable_identical_csv_with_formatting_differences(tmp_path):
    manifest = {"default_month": "2026-09", "available_months": []}
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    _write_bi_bundle_dir(old_dir, manifest,
        root_overrides={"bi_daily_timeseries.csv": "date,count\n2026-09-20,1\n"})
    _write_bi_bundle_dir(new_dir, manifest,
        root_overrides={"bi_daily_timeseries.csv": "date,count\n2026-09-20,1"})
    old_bundle = so.load_current_bi_bundle(old_dir)
    assert so.observe_bi(old=old_bundle, source_dir=new_dir) == so.STATUS_UNCHANGED


def test_observe_bi_changed_on_non_default_month_only_change(tmp_path):
    """Case A (independent review blocker 3): default month (2026-09)
    unchanged, a NON-default month (2026-10) changed -> must be detected."""
    manifest = {"default_month": "2026-09", "available_months": ["2026-09", "2026-10"]}
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    _write_bi_bundle_dir(old_dir, manifest, months_overrides={
        "2026-09": {"bi_snapshot.json": json.dumps({"target_month": "2026-09", "revenue": 100})},
        "2026-10": {"bi_snapshot.json": json.dumps({"target_month": "2026-10", "revenue": 200})},
    })
    _write_bi_bundle_dir(new_dir, manifest, months_overrides={
        "2026-09": {"bi_snapshot.json": json.dumps({"target_month": "2026-09", "revenue": 100})},
        "2026-10": {"bi_snapshot.json": json.dumps({"target_month": "2026-10", "revenue": 999})},
    })
    old_bundle = so.load_current_bi_bundle(old_dir)
    assert so.observe_bi(old=old_bundle, source_dir=new_dir) == so.STATUS_CHANGED


def test_observe_bi_changed_on_timeseries_only_change(tmp_path):
    """Case B (independent review blocker 3): a booking moves from one
    date to another within the same month — bi_monthly_kpi.csv's aggregate
    is unchanged, but bi_daily_timeseries.csv changes -> must be detected."""
    manifest = {"default_month": "2026-09", "available_months": ["2026-09"]}
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    kpi = "month,total_bookings\n2026-09,1\n"
    _write_bi_bundle_dir(old_dir, manifest, months_overrides={"2026-09": {
        "bi_daily_timeseries.csv": "date,new_bookings\n2026-09-20,1\n2026-09-21,0\n",
        "bi_monthly_kpi.csv": kpi,
    }})
    _write_bi_bundle_dir(new_dir, manifest, months_overrides={"2026-09": {
        "bi_daily_timeseries.csv": "date,new_bookings\n2026-09-20,0\n2026-09-21,1\n",
        "bi_monthly_kpi.csv": kpi,
    }})
    old_bundle = so.load_current_bi_bundle(old_dir)
    assert so.observe_bi(old=old_bundle, source_dir=new_dir) == so.STATUS_CHANGED


# ------------------------------------------------- finalized-payload comparison (blocker 2)

def test_bi_finalized_payload_unchanged_despite_bank_sticky_restoration(tmp_path, monkeypatch):
    """Regression test for independent review blocker 2: comparing the
    PRE-publish intermediate (bank fields invalid, exactly as a bare GitHub
    Actions checkout generates with no local bank CSV) against the old
    baseline would wrongly report "changed". Comparing the FINALIZED local
    payload — after publish_r2's sticky-bank restoration, which is what the
    corrected refresh-bi-r2.yml step order actually compares — must report
    "unchanged" when only the (already-public) bank data was restored.

    "Old" (currently public) is built via a REAL prior
    merge_sticky_bank_fields() call — production's actual public snapshot
    always went through this same sticky-merge (GitHub Actions always
    publishes with --preserve-bank-fields-from-r2), so it already carries
    bank_fields_source/bank_fields_preserved_note, not just raw bank_csv_
    import_status/bank_actual_latest_balance. A baseline missing that
    metadata would not be representative of production.
    """
    manifest = {"default_month": "2026-09", "available_months": ["2026-09"]}

    # A genuine earlier Mac `ingest-bank-csv` import (run N-2), borrowed
    # forward by a GitHub Actions run with no local CSV (run N-1) — this is
    # what "currently public" actually looks like in production.
    real_import = {
        "generated_at_jst": "run-N-minus-2",
        "bank_csv_import_status": "imported", "bank_actual_latest_balance": 100,
    }
    old_snapshot = bank_sticky_fields.merge_sticky_bank_fields(
        {"target_month": "2026-09", "revenue": 100, "generated_at_jst": "run-N-minus-1",
         "bank_csv_import_status": "not_available", "bank_actual_latest_balance": None},
        real_import,
    )
    old_dir = tmp_path / "old"
    _write_bi_bundle_dir(old_dir, manifest,
        root_overrides={"bi_snapshot.json": json.dumps(old_snapshot)},
        months_overrides={"2026-09": {"bi_snapshot.json": json.dumps(old_snapshot)}})
    old_bundle = so.load_current_bi_bundle(old_dir)

    # "New" = this run's freshly-generated intermediate: no local bank CSV
    # (GitHub Actions), so bank_* fields are invalid. Business data (revenue)
    # is unchanged from old.
    new_snapshot_pre_publish = {
        "target_month": "2026-09", "revenue": 100,
        "bank_csv_import_status": "not_available", "bank_actual_latest_balance": None,
    }
    new_dir = tmp_path / "new"
    _write_bi_bundle_dir(new_dir, manifest,
        root_overrides={"bi_snapshot.json": json.dumps(new_snapshot_pre_publish)},
        months_overrides={"2026-09": {"bi_snapshot.json": json.dumps(new_snapshot_pre_publish)}})

    # Sanity: comparing the PRE-publish intermediate is exactly the bug
    # blocker 2 fixes — it would wrongly say "changed".
    pre_publish_bundle = so.load_current_bi_bundle(new_dir)
    assert so.compare_bi_bundle(old_bundle, pre_publish_bundle) == so.STATUS_CHANGED

    # Apply publish_r2's actual sticky-bank transformation (what "Publish
    # BI to R2" runs, before "Compare BI snapshot semantics" in the
    # corrected workflow order) — mock the network fetch of the previous
    # public snapshot instead of hitting the real Worker.
    monkeypatch.setattr(publish_r2, "_fetch_public_snapshot", lambda month=None, timeout=20: old_snapshot)
    manifest_for_publish = json.loads((new_dir / "manifest.json").read_text(encoding="utf-8"))
    publish_r2._apply_bank_sticky_fields(new_dir, manifest_for_publish)

    finalized_bundle = so.load_current_bi_bundle(new_dir)
    assert so.compare_bi_bundle(old_bundle, finalized_bundle) == so.STATUS_UNCHANGED


def test_bank_fields_preserved_at_jst_timestamp_is_volatile_not_semantic(tmp_path, monkeypatch):
    """bank_fields_preserved_at_jst is stamped fresh on every sticky-bank
    preservation run, even when the borrowed bank data is byte-for-byte
    identical to last time — it must never by itself cause "changed"."""
    manifest = {"default_month": "2026-09", "available_months": []}
    previous_public = {
        "revenue": 100, "bank_csv_import_status": "imported", "bank_actual_latest_balance": 100,
    }
    monkeypatch.setattr(publish_r2, "_fetch_public_snapshot", lambda month=None, timeout=20: previous_public)

    def build_finalized(tag: str, jst_now: str) -> dict:
        d = tmp_path / tag
        _write_bi_bundle_dir(d, manifest, root_overrides={"bi_snapshot.json": json.dumps(
            {"revenue": 100, "bank_csv_import_status": "not_available", "bank_actual_latest_balance": None})})
        monkeypatch.setattr(bank_sticky_fields, "_jst_now_str", lambda: jst_now)
        m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        publish_r2._apply_bank_sticky_fields(d, m)
        return so.load_current_bi_bundle(d)

    run1 = build_finalized("run1", "2026-09-20T10:00:00+09:00")
    run2 = build_finalized("run2", "2026-09-20T10:15:00+09:00")

    assert run1["root"]["bi_snapshot.json"] != run2["root"]["bi_snapshot.json"], \
        "sanity: the raw preserved-at timestamp really does differ between runs"
    assert so.compare_bi_bundle(run1, run2) == so.STATUS_UNCHANGED


# ---------------------------------------------------------------- observe_staff_ops (error handling)

def test_observe_staff_ops_returns_error_when_current_snapshot_unreadable(tmp_path):
    missing = tmp_path / "does_not_exist.json"
    status = so.observe_staff_ops(old={"dates": {}}, path=missing)
    assert status == so.STATUS_ERROR


def test_observe_staff_ops_changed_on_meaningful_field(tmp_path):
    new_path = tmp_path / "staff_ops_snapshot.json"
    new_path.write_text(json.dumps({
        "generated_at_jst": "t2",
        "dates": {"2026-09-20": {"arrivals": [{"booking_id": "1", "room_number": "402"}]}},
    }), encoding="utf-8")
    old = {
        "generated_at_jst": "t1",
        "dates": {"2026-09-20": {"arrivals": [{"booking_id": "1", "room_number": "401"}]}},
    }
    assert so.observe_staff_ops(old=old, path=new_path) == so.STATUS_CHANGED


# ---------------------------------------------------------------- capture_bi_baseline (full bundle)

def test_capture_bi_baseline_returns_none_on_manifest_http_error(monkeypatch):
    class FakeResp:
        status_code = 500
        text = ""
    monkeypatch.setattr(so.requests, "get", lambda url, timeout=20: FakeResp())
    assert so.capture_bi_baseline() is None


def test_capture_bi_baseline_returns_none_on_exception(monkeypatch):
    import requests as requests_module

    def raise_it(url, timeout=20):
        raise requests_module.RequestException("network down")
    monkeypatch.setattr(so.requests, "get", raise_it)
    assert so.capture_bi_baseline() is None


def test_capture_bi_baseline_fetches_every_root_and_month_file(monkeypatch):
    manifest = {"default_month": "2026-09", "available_months": ["2026-09", "2026-10"]}
    root_texts = {fn: (json.dumps({"file": fn}) if fn.endswith(".json") else f"col\n{fn}\n")
                  for fn in publish_r2.UPLOAD_FILES if fn != "manifest.json"}
    month_texts = {
        month: {fn: (json.dumps({"month": month, "file": fn}) if fn.endswith(".json") else f"col\n{month}-{fn}\n")
                for fn in publish_r2.MONTH_UPLOAD_FILENAMES}
        for month in manifest["available_months"]
    }

    class FakeResp:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

    def fake_get(url, timeout=20):
        if url == f"{so.PUBLIC_API_BASE}/data/manifest.json":
            return FakeResp(json.dumps(manifest))
        for fn, text in root_texts.items():
            if url == f"{so.PUBLIC_API_BASE}/data/{fn}":
                return FakeResp(text)
        for month, files in month_texts.items():
            for fn, text in files.items():
                if url == f"{so.PUBLIC_API_BASE}/data/months/{month}/{fn}":
                    return FakeResp(text)
        raise AssertionError(f"unexpected URL requested: {url}")

    monkeypatch.setattr(so.requests, "get", fake_get)
    bundle = so.capture_bi_baseline()
    assert bundle["root"]["manifest.json"] == json.dumps(manifest)
    for fn, text in root_texts.items():
        assert bundle["root"][fn] == text
    assert set(bundle["months"].keys()) == {"2026-09", "2026-10"}
    for month, files in month_texts.items():
        for fn, text in files.items():
            assert bundle["months"][month][fn] == text


def test_capture_bi_baseline_returns_none_when_any_month_file_is_missing(monkeypatch):
    manifest = {"default_month": "2026-09", "available_months": ["2026-09"]}

    class FakeResp:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

    def fake_get(url, timeout=20):
        if url == f"{so.PUBLIC_API_BASE}/data/manifest.json":
            return FakeResp(json.dumps(manifest))
        if url.startswith(f"{so.PUBLIC_API_BASE}/data/months/"):
            return FakeResp("", status_code=404)
        return FakeResp("{}")

    monkeypatch.setattr(so.requests, "get", fake_get)
    assert so.capture_bi_baseline() is None


# ---------------------------------------------------------------- capture_staff_ops_baseline

def test_capture_staff_ops_baseline_returns_none_when_object_missing(monkeypatch):
    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "Object not found"
    monkeypatch.setattr(so.subprocess, "run", lambda *a, **k: FakeResult())
    assert so.capture_staff_ops_baseline() is None


def test_capture_staff_ops_baseline_returns_parsed_json_on_success(monkeypatch, tmp_path):
    payload = {"dates": {"2026-09-20": {"arrivals": []}}}

    def fake_run(cmd, capture_output, text, timeout):
        # emulate `wrangler r2 object get ... --file <path>` writing the file
        file_arg = cmd[cmd.index("--file") + 1]
        with open(file_arg, "w", encoding="utf-8") as f:
            json.dump(payload, f)

        class FakeResult:
            returncode = 0
        return FakeResult()

    monkeypatch.setattr(so.subprocess, "run", fake_run)
    assert so.capture_staff_ops_baseline() == payload
