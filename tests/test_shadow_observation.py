"""shadow_observation.py — semantic canonicalizer + no-baseline/error handling
for the shadow-mode false-negative observer (KIRAKU BI PHASE 2).

PII guarantee under test: a change in a guest-PII-shaped field (name/phone/
address/comment) must still be detected as "changed" (Staff Ops semantic
comparison must not silently ignore it), but every function here only ever
returns one of the four opaque status strings — never the compared values
themselves. test_pii_value_never_leaks_into_return_value asserts this
directly.
"""
import json

from yuge_finance import shadow_observation as so


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


# ---------------------------------------------------------------- observe_bi / observe_staff_ops (error handling)

def test_observe_bi_returns_error_when_current_snapshot_unreadable(tmp_path):
    # no manifest.json / bi_snapshot.json present at all
    status = so.observe_bi(old={"revenue": 1}, source_dir=tmp_path)
    assert status == so.STATUS_ERROR


def test_observe_bi_no_baseline_when_old_is_none(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"default_month": None}), encoding="utf-8")
    (tmp_path / "bi_snapshot.json").write_text(json.dumps({"revenue": 1}), encoding="utf-8")
    assert so.observe_bi(old=None, source_dir=tmp_path) == so.STATUS_NO_BASELINE


def test_observe_bi_prefers_default_month_snapshot(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"default_month": "2026-09"}), encoding="utf-8")
    (tmp_path / "bi_snapshot.json").write_text(json.dumps({"revenue": 999}), encoding="utf-8")
    month_dir = tmp_path / "months" / "2026-09"
    month_dir.mkdir(parents=True)
    (month_dir / "bi_snapshot.json").write_text(json.dumps({"revenue": 100}), encoding="utf-8")
    # old matches the MONTH snapshot's revenue, not the root file's -> unchanged
    assert so.observe_bi(old={"revenue": 100}, source_dir=tmp_path) == so.STATUS_UNCHANGED


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


# ---------------------------------------------------------------- capture_bi_baseline / capture_staff_ops_baseline

def test_capture_bi_baseline_returns_none_on_http_error(monkeypatch):
    class FakeResp:
        status_code = 500
        def json(self):
            raise AssertionError("must not be called on error")
    monkeypatch.setattr(so.requests, "get", lambda url, timeout=20: FakeResp())
    assert so.capture_bi_baseline() is None


def test_capture_bi_baseline_returns_none_on_exception(monkeypatch):
    import requests as requests_module

    def raise_it(url, timeout=20):
        raise requests_module.RequestException("network down")
    monkeypatch.setattr(so.requests, "get", raise_it)
    assert so.capture_bi_baseline() is None


def test_capture_bi_baseline_returns_parsed_json_on_success(monkeypatch):
    class FakeResp:
        status_code = 200
        def json(self):
            return {"revenue": 42}
    monkeypatch.setattr(so.requests, "get", lambda url, timeout=20: FakeResp())
    assert so.capture_bi_baseline() == {"revenue": 42}


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
