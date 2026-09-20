"""refresh-bi-r2.yml の event-driven BI refresh移行（BiRefreshCoordinator）対応部分の安全性テスト。

既存の test_github_actions_bi_refresh.py（後方互換・既存挙動の保証）とは別に、
今回追加した workflow_dispatch inputs / completion callback step だけを対象にする。
"""
import yaml

from yuge_finance import config

WORKFLOW_PATH = config.ROOT / ".github" / "workflows" / "refresh-bi-r2.yml"


def _load_workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _raw_text():
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _job():
    return _load_workflow()["jobs"]["refresh-bi-r2"]


def _callback_step():
    return next(s for s in _job()["steps"] if s.get("name") == "Report completion to BI Refresh Coordinator")


def test_workflow_dispatch_has_optional_coordinator_inputs():
    wf = _load_workflow()
    on = wf.get("on", wf.get(True))
    inputs = on["workflow_dispatch"]["inputs"]
    for name in ("dispatch_id", "target_seq", "reason"):
        assert name in inputs
        assert inputs[name].get("required") is False


def test_manual_workflow_run_without_inputs_is_still_valid():
    """`gh workflow run refresh-bi-r2.yml`（inputs無し）が引き続き動く後方互換の要。"""
    wf = _load_workflow()
    on = wf.get("on", wf.get(True))
    inputs = on["workflow_dispatch"]["inputs"]
    for name in ("dispatch_id", "target_seq", "reason"):
        assert inputs[name].get("default") is not None


def test_refresh_and_publish_steps_have_stable_ids_for_outcome_tracking():
    job = _job()
    refresh_step = next(s for s in job["steps"] if s.get("name") == "Refresh BI from Beds24")
    publish_step = next(s for s in job["steps"] if s.get("name") == "Publish BI to R2")
    assert refresh_step.get("id") == "refresh_bi"
    assert publish_step.get("id") == "publish_r2"


def test_completion_callback_step_exists_and_runs_always():
    step = _callback_step()
    assert step.get("if") == "always()"


def test_completion_callback_uses_bi_refresh_callback_secret():
    text = _raw_text()
    assert "secrets.BI_REFRESH_CALLBACK_SECRET" in text


def test_completion_callback_skips_when_dispatch_id_is_empty():
    """Coordinator起点ではない実行（手動実行/後方互換）ではcallback送信自体をスキップする。"""
    run_text = _callback_step()["run"]
    assert 'if [ -z "${DISPATCH_ID:-}" ]; then' in run_text
    assert "skipping completion callback" in run_text


def test_completion_callback_never_echoes_the_secret_or_the_payload():
    run_text = _callback_step()["run"]
    assert "echo \"$BI_REFRESH_CALLBACK_SECRET\"" not in run_text
    assert "echo \"$payload\"" not in run_text
    assert "echo $payload" not in run_text


def test_completion_callback_reports_status_via_actual_step_outcomes_not_job_status():
    """job.statusではなくrefresh_bi/publish_r2の実outcomeで成否判定する
    （Staff Ops steps等、他のif-gatedステップの状態に引きずられないため）。"""
    step = _callback_step()
    assert step["env"]["REFRESH_OUTCOME"] == "${{ steps.refresh_bi.outcome }}"
    assert step["env"]["PUBLISH_OUTCOME"] == "${{ steps.publish_r2.outcome }}"


def test_completion_callback_step_always_exits_zero():
    """callback自体の成否がjobの合否を左右してはいけない（項目15）。"""
    run_text = _callback_step()["run"]
    assert run_text.strip().endswith("exit 0")


def test_completion_callback_uses_jq_not_a_fragile_heredoc():
    run_text = _callback_step()["run"]
    assert "jq -n" in run_text


def test_workflow_still_never_deploys_worker_or_closes_month():
    """既存の禁止事項テストが今回の追加分でも成立することを再確認する。"""
    text = _raw_text()
    for forbidden in ("wrangler deploy", "close-month", "build-ledger", "export-excel"):
        assert forbidden not in text
