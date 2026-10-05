"""deploy-worker.yml: BI R2バケットのr2.dev公開が無効であることをdeploy前に読み取り検証する。

BIバケットには財務・予約情報が入る。Worker内のadmin gateは、Workerのbindingを経由しない
経路（r2.dev public URL）には効かないため、その経路が閉じていることをdeployの前提にする。
"""
import yaml

from yuge_finance import config

WORKFLOW_PATH = config.ROOT / ".github" / "workflows" / "deploy-worker.yml"
GUARD_NAME = "Verify BI R2 bucket has NO public access (r2.dev disabled, read-only)"


def _steps():
    wf = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return wf["jobs"]["deploy"]["steps"]


def _guard():
    return next(s for s in _steps() if s.get("name") == GUARD_NAME)


def test_guard_runs_before_the_worker_deploy():
    names = [s.get("name") for s in _steps()]
    assert GUARD_NAME in names
    assert names.index(GUARD_NAME) < names.index("Deploy Worker")


def test_guard_checks_the_bi_bucket_and_has_credentials():
    guard = _guard()
    assert "dev-url get kiraku-bi-data" in guard["run"]
    assert guard["working-directory"] == "cloudflare/bi-web"
    assert set(guard["env"]) >= {"CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"}


def test_guard_is_read_only():
    # echo行（運用者への案内文）は除き、実際に実行されるコマンドだけを見る。
    commands = "\n".join(
        line for line in _guard()["run"].splitlines() if not line.strip().startswith("echo"))
    for forbidden in ("dev-url disable", "dev-url enable", "bucket delete", "bucket create", "domain add"):
        assert forbidden not in commands


def test_guard_fails_closed_when_enabled_or_unverifiable():
    run = _guard()["run"]
    assert run.startswith("set -euo pipefail")
    assert "is enabled" in run and "is disabled" in run
    # enabled / status unreadable / unexpected format -> every branch stops the deploy
    assert run.count("exit 1") >= 3
    assert _guard().get("continue-on-error") is not True


def test_guard_does_not_echo_raw_wrangler_output_into_the_public_log():
    """r2.devが有効だった場合、wranglerの出力には公開URLが含まれる。公開リポジトリのログへ出さない。"""
    run = _guard()["run"]
    assert "| tee" not in run
    assert "<url-redacted>" in run
