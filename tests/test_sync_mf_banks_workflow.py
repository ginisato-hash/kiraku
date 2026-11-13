"""Execution/privacy contracts for the isolated MF workflow."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/sync-mf-banks.yml"


def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def step(identifier):
    return next(item for item in workflow()["jobs"]["sync-mf-banks"]["steps"] if item.get("id") == identifier)


def run_step(item, tmp_path, overrides=None):
    environment = {
        "PATH": os.environ["PATH"],
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "RUNNER_TEMP": str(tmp_path),
    }
    environment.update(overrides or {})
    return subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", item["run"]],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
    )


def test_separate_single_writer_manual_and_hourly_schedule():
    definition = workflow()
    triggers = definition.get("on", definition.get(True))
    assert {item["cron"] for item in triggers["schedule"]} == {"17 0-17,19-23 * * *", "17 18 * * *"}
    assert triggers["workflow_dispatch"]["inputs"]["full"]["type"] == "boolean"
    assert definition["concurrency"] == {"group": "sync-mf-banks-private", "cancel-in-progress": False}
    assert definition["permissions"] == {"contents": "read"}


def test_missing_secret_is_clean_skip_and_never_prints_config_values(tmp_path):
    result = run_step(step("gate"), tmp_path, {"MF_API_KEY": "synthetic-secret"})
    assert result.returncode == 0
    assert (tmp_path / "output").read_text() == "enabled=false\n"
    assert "MF_SYNC_SKIPPED_MISSING_SECRET:MF_OFFICE_CODE" in result.stdout
    assert "synthetic-secret" not in result.stdout + result.stderr
    assert "MF_SYNC_SKIPPED_MISSING_SECRET:MF_API_KEY" not in result.stdout


def test_whitespace_secret_is_missing_and_all_secrets_gate_before_network(tmp_path):
    secret_names = step("gate")["env"].keys()
    values = {name: "synthetic-value" for name in secret_names}
    values["MF_OFFICE_CODE"] = "  "
    result = run_step(step("gate"), tmp_path, values)
    assert result.stdout.strip() == "MF_SYNC_SKIPPED_MISSING_SECRET:MF_OFFICE_CODE"
    assert (tmp_path / "output").read_text() == "enabled=false\n"
    all_steps = workflow()["jobs"]["sync-mf-banks"]["steps"]
    assert all_steps[0]["id"] == "gate"
    for item in all_steps[1:]:
        if item.get("name") != "Preserve sync failure status":
            assert "steps.gate.outputs.enabled == 'true'" in item["if"]


@pytest.mark.parametrize("manual,schedule,full", [
    ("", "17 0-17,19-23 * * *", "false"),
    ("", "17 18 * * *", "true"),
    ("true", "", "true"),
    ("false", "", "false"),
])
def test_full_reconciliation_is_selected_by_trigger_not_wall_clock(tmp_path, manual, schedule, full):
    result = run_step(step("mode"), tmp_path, {"MF_MANUAL_FULL": manual, "MF_EVENT_SCHEDULE": schedule})
    assert result.returncode == 0
    assert (tmp_path / "output").read_text() == "full=" + full + "\n"


@pytest.mark.parametrize("exit_code", [0, 7])
def test_sync_wrapper_suppresses_payload_even_on_cli_failure(tmp_path, exit_code):
    binary = tmp_path / "yuge-finance"
    binary.write_text(
        "#!/bin/sh\nprintf 'synthetic-account-and-amount\\n'\n"
        "printf 'synthetic-secret-key\\n' >&2\nexit " + str(exit_code) + "\n"
    )
    binary.chmod(0o700)
    result = run_step(step("sync"), tmp_path, {
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "MF_FULL_RECONCILIATION": "true",
    })
    assert result.returncode == exit_code
    assert "synthetic" not in result.stdout + result.stderr
    assert (tmp_path / "output").read_text() == f"exit_code={exit_code}\n"


@pytest.mark.parametrize("updated", [False, True])
def test_initialization_failure_cannot_republish_the_old_summary(tmp_path, updated):
    directory = tmp_path / "data/private/moneyforward"
    directory.mkdir(parents=True)
    old = b'{"status":"old","synthetic_amount":12345}'
    (tmp_path / "mf-summary-before.sha256").write_text(hashlib.sha256(old).hexdigest())
    (directory / "summary.json").write_bytes(b'{"status":"failed","attempted_at":"new"}' if updated else old)
    result = run_step(step("changed"), tmp_path)
    assert result.returncode == (0 if updated else 1)
    assert (tmp_path / "output").read_text() == "changed=" + ("true" if updated else "false") + "\n"
    assert "12345" not in result.stdout + result.stderr
    assert "steps.changed.outputs.changed == 'true'" in step("publish")["if"]


def test_publish_requires_successful_restore_but_accepts_a_new_failed_sync_status():
    assert step("sync")["continue-on-error"] is True
    assert "steps.restore.outcome == 'success'" in step("changed")["if"]
    assert "steps.sync.outcome == 'failure'" in step("changed")["if"]
    assert "steps.restore.outcome == 'success'" in step("publish")["if"]
    assert "!cancelled()" in step("publish")["if"]


def test_no_public_finance_upload_raw_log_artifacts_or_unrelated_refresh():
    raw = WORKFLOW.read_text()
    for forbidden in (
        "upload-artifact", "download-artifact", "actions/cache", "tee ",
        "wrangler", "kiraku-bi-data", "CLOUDFLARE_R2_BUCKET", "BEDS24",
        "publish-bi-r2", "build-ledger", "close-month",
    ):
        assert forbidden not in raw
    assert "> /dev/null 2>&1" in step("sync")["run"]
    assert set(step("restore")["env"]) == {"CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"}
    assert set(step("publish")["env"]) == {"CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"}
    assert "CLOUDFLARE_API_TOKEN" not in step("sync")["env"]
    cleanup = workflow()["jobs"]["sync-mf-banks"]["steps"][-1]
    assert "always()" in cleanup["if"]
    assert "rm -rf -- data/private/moneyforward" in cleanup["run"]
