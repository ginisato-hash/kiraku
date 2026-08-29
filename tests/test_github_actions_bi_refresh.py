"""GitHub Actions BI自動更新workflow（.github/workflows/refresh-bi-r2.yml）の安全性テスト。

Mac launchd依存を廃止し、日付跨ぎ/Mac sleep問題を回避するためGitHub Actionsへ移行した。
15分ごとにBeds24取得→BI生成→R2 publishのみを行い、仕訳/PL/BS/CF/Excel/close-monthは
一切実行しないことをworkflow定義そのもので保証する。
"""
import yaml

from yuge_finance import config

WORKFLOW_PATH = config.ROOT / ".github" / "workflows" / "refresh-bi-r2.yml"


def _load_workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _raw_text():
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_workflow_file_exists():
    assert WORKFLOW_PATH.exists()


def test_workflow_has_no_schedule_trigger():
    """2026-08-29: GitHub Actions自身のon.scheduleは信頼できないと判明した
    （fc1ffca参照：15分cronのはずが実際は549回期待に対し実行100回、最大687分の
    silent gap）。15分起動はCloudflare Cron Trigger
    （cloudflare/bi-web/wrangler.toml [triggers] crons）がworkflow_dispatchを
    叩く形に一本化し、本番検証済み（手動+自律発火とも成功、freshness ~1-2分）。
    on.scheduleとの二重発火を避けるため、ここでは完全に撤去したことを保証する。
    """
    wf = _load_workflow()
    # YAMLの "on" キーはPyYAMLでbool Trueとしてパースされることがあるため両対応する。
    on = wf.get("on", wf.get(True))
    assert "schedule" not in on
    assert "cron:" not in _raw_text()


def test_workflow_has_workflow_dispatch():
    wf = _load_workflow()
    on = wf.get("on", wf.get(True))
    assert "workflow_dispatch" in on


def test_workflow_runs_refresh_beds24_bi_with_auto_discovery_and_publish():
    text = _raw_text()
    assert "refresh-beds24-bi --auto-months-with-bookings --publish" in text


def test_workflow_runs_publish_bi_r2():
    text = _raw_text()
    assert "publish-bi-r2" in text


def test_workflow_uses_preserve_bank_fields_from_r2():
    """GitHub Actionsはローカル銀行CSVが無いため、既存R2 snapshotの銀行CFを引き継ぐ。"""
    text = _raw_text()
    assert "publish-bi-r2 --bucket \"$CLOUDFLARE_R2_BUCKET\" --preserve-bank-fields-from-r2" in text


def test_workflow_never_deploys_worker_or_closes_month():
    text = _raw_text()
    for forbidden in ("wrangler deploy", "close-month", "build-ledger", "export-excel"):
        assert forbidden not in text


def test_workflow_permissions_are_read_only():
    wf = _load_workflow()
    assert wf.get("permissions") == {"contents": "read"}


def test_workflow_sets_jst_timezone():
    text = _raw_text()
    assert "TZ: Asia/Tokyo" in text


def test_workflow_has_concurrency_guard():
    wf = _load_workflow()
    assert "concurrency" in wf
    assert wf["concurrency"].get("group") == "refresh-bi-r2"


def test_workflow_has_timeout():
    wf = _load_workflow()
    job = wf["jobs"]["refresh-bi-r2"]
    assert isinstance(job.get("timeout-minutes"), int)
    assert job["timeout-minutes"] > 0


def test_workflow_validates_required_secrets():
    text = _raw_text()
    for secret in ("BEDS24_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
                  "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_R2_BUCKET"):
        assert f"secrets.{secret}" in text


def test_workflow_maps_beds24_api_token_secret_to_long_life_token_env():
    """コードが実際に読むのはBEDS24_LONG_LIFE_TOKEN（Secret名はBEDS24_API_TOKENで統一）。"""
    text = _raw_text()
    assert "BEDS24_LONG_LIFE_TOKEN: ${{ secrets.BEDS24_API_TOKEN }}" in text


def test_workflow_pins_beds24_property_id_to_kiraku_only():
    """他物件を誤取得しないよう、プロパティIDを明示固定する。"""
    text = _raw_text()
    assert 'BEDS24_PROPERTY_IDS: "330695"' in text


def test_workflow_reports_which_secret_is_missing_without_printing_values():
    """Secret未設定時にどのsecretが欠けているか分かる出力にする（値そのものは出さない）。"""
    text = _raw_text()
    assert "::error::$name is missing" in text
    assert "for name in BEDS24_API_TOKEN CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN CLOUDFLARE_R2_BUCKET" in text
    # secret値そのものをechoする行が無いこと（名前のみ報告する設計を保証）
    assert 'echo "$BEDS24_API_TOKEN"' not in text
    assert 'echo "$CLOUDFLARE_API_TOKEN"' not in text


def test_workflow_installs_node_and_wrangler_for_publish_bi_r2():
    wf = _load_workflow()
    job = wf["jobs"]["refresh-bi-r2"]
    step_names = [s.get("name") for s in job["steps"]]
    assert "Set up Node (for wrangler)" in step_names
    assert "Install Wrangler" in step_names


def test_workflow_job_summary_reports_job_status_and_runs_always():
    wf = _load_workflow()
    job = wf["jobs"]["refresh-bi-r2"]
    summary_step = next(s for s in job["steps"] if s.get("name") == "Write job summary")
    assert summary_step.get("if") == "always()"
    assert "job.status" in summary_step["run"]


def test_workflow_has_verify_public_manifest_step():
    wf = _load_workflow()
    job = wf["jobs"]["refresh-bi-r2"]
    step_names = [s.get("name") for s in job["steps"]]
    assert "Verify public manifest" in step_names


def test_public_manifest_verification_is_non_fatal():
    """R2 publishが成功していれば、public API側の403等だけでworkflow全体をfailedにしない。"""
    wf = _load_workflow()
    job = wf["jobs"]["refresh-bi-r2"]
    verify_step = next(s for s in job["steps"] if s.get("name") == "Verify public manifest")
    assert verify_step.get("continue-on-error") is True


def test_public_manifest_verification_sends_custom_user_agent():
    """CloudflareがPython urllibの既定UAを弾く事例があるため、User-Agentを明示する。"""
    wf = _load_workflow()
    job = wf["jobs"]["refresh-bi-r2"]
    verify_step = next(s for s in job["steps"] if s.get("name") == "Verify public manifest")
    assert "User-Agent" in verify_step["run"]
    assert "kiraku-bi-refresh-github-actions" in verify_step["run"]


def test_public_manifest_verification_catches_http_error_as_warning():
    wf = _load_workflow()
    job = wf["jobs"]["refresh-bi-r2"]
    verify_step = next(s for s in job["steps"] if s.get("name") == "Verify public manifest")
    assert "urllib.error.HTTPError" in verify_step["run"]
    assert "::warning::" in verify_step["run"]


def test_write_job_summary_does_not_fail_on_public_api_error():
    """Write job summaryはpublic API失敗(403等)でstep自体が落ちない(|| フォールバックがある)。"""
    wf = _load_workflow()
    job = wf["jobs"]["refresh-bi-r2"]
    summary_step = next(s for s in job["steps"] if s.get("name") == "Write job summary")
    run_text = summary_step["run"]
    assert "User-Agent" in run_text
    assert run_text.count("|| echo") >= 2  # manifest確認・snapshot確認それぞれにフォールバックがある


def test_publish_step_is_the_strict_success_gate():
    """主検証はpublish-bi-r2自体のexit codeに委ねる(continue-on-errorが付いていない)。"""
    wf = _load_workflow()
    job = wf["jobs"]["refresh-bi-r2"]
    publish_step = next(s for s in job["steps"] if s.get("name") == "Publish BI to R2")
    assert publish_step.get("continue-on-error") is not True
    refresh_step = next(s for s in job["steps"] if s.get("name") == "Refresh BI from Beds24")
    assert refresh_step.get("continue-on-error") is not True
