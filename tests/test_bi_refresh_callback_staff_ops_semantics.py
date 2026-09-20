"""Behavioral proof for refresh-bi-r2.yml's "Report completion to BI Refresh
Coordinator" step (fix round blocker 2): a Staff Ops publish failure must
never be reported to the Coordinator as an overall success, and a
legitimately-skipped Staff Ops (gate closed) must never be reported as a
failure.

This does not just assert on the YAML text — it actually EXECUTES the
step's real shell script (extracted verbatim from the workflow file) with a
stubbed `curl` on PATH, for every required outcome combination, and reads
back the "dispatch_status=" the script itself computed and would have sent
to the Coordinator.
"""
import os
import re
import stat
import subprocess
import sys
import textwrap

import pytest
import yaml

from yuge_finance import config

WORKFLOW_PATH = config.ROOT / ".github" / "workflows" / "refresh-bi-r2.yml"


def _callback_step_script() -> str:
    wf = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    job = wf["jobs"]["refresh-bi-r2"]
    step = next(s for s in job["steps"] if s.get("name") == "Report completion to BI Refresh Coordinator")
    return step["run"]


def _make_fake_curl(bin_dir, http_code="200"):
    """A stand-in for curl that never touches the network: it just prints
    the requested -w format (only %{http_code} is used by the real script)
    and captures the payload it was given, for optional inspection."""
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        # Find the -d argument (the JSON payload) and save it for inspection.
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


def _run_callback_step(tmp_path, env_overrides, http_code="200"):
    _make_fake_curl(tmp_path, http_code=http_code)
    script_path = tmp_path / "callback_step.sh"
    script_path.write_text("#!/bin/bash\n" + _callback_step_script())
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)

    env = dict(os.environ)
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"
    env.update({
        "DISPATCH_ID": "test-dispatch-id",
        "TARGET_SEQ": "5",
        "REASON": "booking_webhook",
        "BI_REFRESH_CALLBACK_SECRET": "dummy-secret-for-local-script-test",
        "REFRESH_OUTCOME": "success",
        "PUBLISH_OUTCOME": "success",
        "STAFF_OPS_EXPORT_OUTCOME": "skipped",
        "STAFF_OPS_R2_BUCKET_OUTCOME": "skipped",
        "STAFF_OPS_R2_PUBLISH_OUTCOME": "skipped",
    })
    env.update(env_overrides)

    result = subprocess.run(
        ["bash", str(script_path)], env=env, capture_output=True, text=True, timeout=30)
    return result


def _dispatch_status(result) -> str:
    m = re.search(r"dispatch_status=(\S+)", result.stdout)
    assert m, f"script did not print a dispatch_status line; stdout={result.stdout!r} stderr={result.stderr!r}"
    return m.group(1)


def test_bi_success_and_staff_ops_publish_failure_is_reported_as_failure(tmp_path):
    result = _run_callback_step(tmp_path, {
        "STAFF_OPS_EXPORT_OUTCOME": "success",
        "STAFF_OPS_R2_BUCKET_OUTCOME": "success",
        "STAFF_OPS_R2_PUBLISH_OUTCOME": "failure",
    })
    assert result.returncode == 0, "the step itself must always exit 0"
    assert _dispatch_status(result) == "failure"


def test_bi_success_and_staff_ops_disabled_is_reported_as_success(tmp_path):
    result = _run_callback_step(tmp_path, {
        "STAFF_OPS_EXPORT_OUTCOME": "skipped",
        "STAFF_OPS_R2_BUCKET_OUTCOME": "skipped",
        "STAFF_OPS_R2_PUBLISH_OUTCOME": "skipped",
    })
    assert result.returncode == 0
    assert _dispatch_status(result) == "success"


@pytest.mark.parametrize("failing_step", ["REFRESH_OUTCOME", "PUBLISH_OUTCOME"])
def test_bi_failure_is_reported_as_failure(tmp_path, failing_step):
    result = _run_callback_step(tmp_path, {failing_step: "failure"})
    assert result.returncode == 0
    assert _dispatch_status(result) == "failure"


def test_all_enabled_critical_steps_succeeding_is_reported_as_success(tmp_path):
    result = _run_callback_step(tmp_path, {
        "STAFF_OPS_EXPORT_OUTCOME": "success",
        "STAFF_OPS_R2_BUCKET_OUTCOME": "success",
        "STAFF_OPS_R2_PUBLISH_OUTCOME": "success",
    })
    assert result.returncode == 0
    assert _dispatch_status(result) == "success"


def test_staff_ops_export_failure_alone_is_reported_as_failure(tmp_path):
    result = _run_callback_step(tmp_path, {
        "STAFF_OPS_EXPORT_OUTCOME": "failure",
        "STAFF_OPS_R2_BUCKET_OUTCOME": "skipped",
        "STAFF_OPS_R2_PUBLISH_OUTCOME": "skipped",
    })
    assert result.returncode == 0
    assert _dispatch_status(result) == "failure"


def test_staff_ops_r2_bucket_ensure_failure_alone_is_reported_as_failure(tmp_path):
    result = _run_callback_step(tmp_path, {
        "STAFF_OPS_EXPORT_OUTCOME": "success",
        "STAFF_OPS_R2_BUCKET_OUTCOME": "failure",
        "STAFF_OPS_R2_PUBLISH_OUTCOME": "skipped",
    })
    assert result.returncode == 0
    assert _dispatch_status(result) == "failure"


def test_a_cancelled_staff_ops_step_is_treated_as_failure_not_success(tmp_path):
    """"cancelled" is neither "success" nor "skipped" — must not be waved through."""
    result = _run_callback_step(tmp_path, {
        "STAFF_OPS_EXPORT_OUTCOME": "success",
        "STAFF_OPS_R2_BUCKET_OUTCOME": "success",
        "STAFF_OPS_R2_PUBLISH_OUTCOME": "cancelled",
    })
    assert result.returncode == 0
    assert _dispatch_status(result) == "failure"


def test_the_script_never_echoes_the_secret_value(tmp_path):
    result = _run_callback_step(tmp_path, {})
    assert "dummy-secret-for-local-script-test" not in result.stdout
    assert "dummy-secret-for-local-script-test" not in result.stderr


def test_manual_run_with_no_dispatch_id_skips_before_any_status_computation(tmp_path):
    result = _run_callback_step(tmp_path, {"DISPATCH_ID": ""})
    assert result.returncode == 0
    assert "skipping completion callback" in result.stdout
    assert "dispatch_status=" not in result.stdout
