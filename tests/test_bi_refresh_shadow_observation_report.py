"""Behavioral proof for refresh-bi-r2.yml's shadow-observation pipeline
(KIRAKU BI PHASE 2, independent review blockers 2/4/5):

1. Step order — the semantic compare step for each component must run
   AFTER that component's R2 publish step, so it compares the finalized
   published payload (post sticky-bank restoration for BI), not a
   pre-publish intermediate GitHub Actions never actually serves.
2. "Report shadow observation to BI Refresh Coordinator" — like
   test_bi_refresh_callback_staff_ops_semantics.py does for the completion
   callback step, this EXECUTES the step's real shell script (extracted
   verbatim from the workflow file) with a stubbed `curl` on PATH, so the
   status-defaulting/dispatch_id-forwarding logic is proven, not just
   asserted from the YAML text.
"""
import os
import re
import stat
import subprocess
import textwrap

import yaml

from yuge_finance import config

WORKFLOW_PATH = config.ROOT / ".github" / "workflows" / "refresh-bi-r2.yml"


def _load_workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _step_names(job):
    return [s.get("name") for s in job["steps"]]


def _step_index(job, name):
    names = _step_names(job)
    assert name in names, f"step {name!r} not found; steps are {names}"
    return names.index(name)


def test_bi_compare_runs_after_bi_publish():
    job = _load_workflow()["jobs"]["refresh-bi-r2"]
    assert _step_index(job, "Publish BI to R2") < _step_index(job, "Compare BI snapshot semantics (shadow observation)")


def test_staff_ops_compare_runs_after_staff_ops_publish():
    job = _load_workflow()["jobs"]["refresh-bi-r2"]
    assert (_step_index(job, "Publish Daily Ops data to R2")
            < _step_index(job, "Compare Staff Ops snapshot semantics (shadow observation)"))


def test_bi_baseline_capture_runs_before_bi_refresh_and_publish():
    job = _load_workflow()["jobs"]["refresh-bi-r2"]
    baseline_idx = _step_index(job, "Capture previous BI snapshot (shadow observation baseline)")
    assert baseline_idx < _step_index(job, "Refresh BI from Beds24")
    assert baseline_idx < _step_index(job, "Publish BI to R2")


def _shadow_observation_step_script() -> str:
    wf = _load_workflow()
    job = wf["jobs"]["refresh-bi-r2"]
    step = next(s for s in job["steps"] if s.get("name") == "Report shadow observation to BI Refresh Coordinator")
    return step["run"]


def _make_fake_curl(bin_dir, http_code="200"):
    """Stand-in for curl that never touches the network — prints the
    requested http_code and saves the -d payload for inspection, exactly
    like test_bi_refresh_callback_staff_ops_semantics.py's helper."""
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        for i in "$@"; do
            if [ "$prev" = "-d" ]; then
                printf '%s' "$i" > "{bin_dir}/last_payload.json"
            fi
            prev="$i"
        done
        printf '%s' "{http_code}"
        exit 0
        """))
    fake_curl.chmod(fake_curl.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_shadow_observation_step(tmp_path, env_overrides, http_code="200"):
    _make_fake_curl(tmp_path, http_code=http_code)
    script_path = tmp_path / "shadow_observation_step.sh"
    script_path.write_text("#!/bin/bash\n" + _shadow_observation_step_script())
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)

    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"
    env.update({
        "DISPATCH_ID": "test-dispatch-id",
        "REASON": "booking_webhook",
        "BI_REFRESH_CALLBACK_SECRET": "dummy-secret-for-local-script-test",
        "BI_STATUS": "unchanged",
        "STAFF_OPS_STATUS": "unchanged",
        "STAFF_OPS_GATE_OPEN": "1",
    })
    env.update(env_overrides)

    result = subprocess.run(
        ["bash", str(script_path)], env=env, capture_output=True, text=True, timeout=30)
    return result


def _reported_statuses(result):
    m = re.search(r"bi_status=(\S+) staff_ops_status=(\S+)", result.stdout)
    assert m, f"script did not print the expected status line; stdout={result.stdout!r} stderr={result.stderr!r}"
    return m.group(1), m.group(2)


def _last_payload(tmp_path):
    import json
    return json.loads((tmp_path / "last_payload.json").read_text(encoding="utf-8"))


def test_normal_case_statuses_pass_through_unchanged(tmp_path):
    result = _run_shadow_observation_step(tmp_path, {"BI_STATUS": "changed", "STAFF_OPS_STATUS": "changed"})
    assert result.returncode == 0
    assert _reported_statuses(result) == ("changed", "changed")


def test_bi_publish_failure_manifests_as_empty_bi_status_and_is_reported_as_error(tmp_path):
    """When "Publish BI to R2" fails, the (implicitly success()-gated)
    "Compare BI snapshot semantics" step never runs, so BI_STATUS arrives
    empty here — this must be reported as bi_status=error, never as a
    real changed/unchanged verdict (independent review blocker 5)."""
    result = _run_shadow_observation_step(tmp_path, {"BI_STATUS": ""})
    assert result.returncode == 0
    bi_status, _ = _reported_statuses(result)
    assert bi_status == "error"


def test_staff_ops_publish_failure_with_gate_open_manifests_as_error_not_skipped(tmp_path):
    """When the STAFF_OPS_AUTH_CONFIRMED gate is open but a publish step
    (export/bucket/R2 publish) fails, the compare step is skipped and
    STAFF_OPS_STATUS arrives empty — this must be reported as
    staff_ops_status=error, never silently downgraded to "skipped"
    (independent review blocker 5)."""
    result = _run_shadow_observation_step(tmp_path, {"STAFF_OPS_STATUS": "", "STAFF_OPS_GATE_OPEN": "1"})
    assert result.returncode == 0
    _, staff_ops_status = _reported_statuses(result)
    assert staff_ops_status == "error"


def test_staff_ops_gate_closed_with_empty_status_is_reported_as_skipped(tmp_path):
    """When the gate is simply closed, the compare step legitimately never
    runs — that must still report staff_ops_status=skipped, not error."""
    result = _run_shadow_observation_step(tmp_path, {"STAFF_OPS_STATUS": "", "STAFF_OPS_GATE_OPEN": ""})
    assert result.returncode == 0
    _, staff_ops_status = _reported_statuses(result)
    assert staff_ops_status == "skipped"


def test_dispatch_id_is_forwarded_in_the_payload(tmp_path):
    result = _run_shadow_observation_step(tmp_path, {"DISPATCH_ID": "dispatch-xyz"})
    assert result.returncode == 0
    payload = _last_payload(tmp_path)
    assert payload["dispatch_id"] == "dispatch-xyz"


def test_empty_reason_defaults_to_shadow_unconditional_in_the_payload(tmp_path):
    result = _run_shadow_observation_step(tmp_path, {"REASON": ""})
    assert result.returncode == 0
    payload = _last_payload(tmp_path)
    assert payload["reason"] == "shadow_unconditional"


def test_manual_run_with_no_dispatch_id_skips_before_any_status_computation(tmp_path):
    result = _run_shadow_observation_step(tmp_path, {"DISPATCH_ID": ""})
    assert result.returncode == 0
    assert "skipping shadow observation report" in result.stdout
    assert "bi_status=" not in result.stdout


def test_the_script_never_echoes_the_secret_value(tmp_path):
    result = _run_shadow_observation_step(tmp_path, {})
    assert "dummy-secret-for-local-script-test" not in result.stdout
    assert "dummy-secret-for-local-script-test" not in result.stderr
