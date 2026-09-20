"""scripts/run_bi_refresh_once.sh が、.github/workflows/refresh-bi-r2.yml の
実際のビジネスロジック（Beds24取得→BI publish→(gated) Staff Ops export/publish）
と同じコマンドを実行することを保証する。

このスクリプトは今回まだworkflow本体には配線しない（既存の
test_github_actions_bi_refresh.pyがworkflow YAMLの文字列に厳密依存している
ため、配線変更は別途大規模リファクタとして扱う — 項目26参照）。ここでは
将来Cloud Runから直接呼べる1-shot実行として、ワークフローと処理内容が
食い違っていないことだけを検証する。
"""
import stat

from yuge_finance import config

SCRIPT_PATH = config.ROOT / "scripts" / "run_bi_refresh_once.sh"
WORKFLOW_PATH = config.ROOT / ".github" / "workflows" / "refresh-bi-r2.yml"


def _script_text():
    return SCRIPT_PATH.read_text(encoding="utf-8")


def _workflow_text():
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_script_exists_and_is_executable():
    assert SCRIPT_PATH.exists()
    mode = SCRIPT_PATH.stat().st_mode
    assert mode & stat.S_IXUSR


def test_script_runs_the_same_refresh_and_publish_commands_as_the_workflow():
    text = _script_text()
    assert "yuge-finance refresh-beds24-bi --auto-months-with-bookings --publish" in text
    assert 'yuge-finance publish-bi-r2 --bucket "$CLOUDFLARE_R2_BUCKET" --preserve-bank-fields-from-r2' in text
    # 同じ文字列がworkflow側にも存在すること（食い違い検出）。
    wf_text = _workflow_text()
    assert "refresh-beds24-bi --auto-months-with-bookings --publish" in wf_text
    assert 'publish-bi-r2 --bucket "$CLOUDFLARE_R2_BUCKET" --preserve-bank-fields-from-r2' in wf_text


def test_script_gates_staff_ops_on_the_same_variable_as_the_workflow():
    text = _script_text()
    assert 'STAFF_OPS_AUTH_CONFIRMED' in text
    assert "export-daily-ops" in text
    assert "kiraku-staff-ops-data" in text
    assert "staff_ops_snapshot.json" in text


def test_script_never_runs_accounting_or_deploy_commands():
    text = _script_text()
    for forbidden in ("wrangler deploy", "close-month", "build-ledger", "export-excel"):
        assert forbidden not in text


def test_script_defaults_to_jst_timezone():
    assert 'TZ="${TZ:-Asia/Tokyo}"' in _script_text()


def test_script_is_not_the_macos_launchd_fallback_script():
    """既存のscripts/refresh_beds24_bi_and_publish_r2.sh（macOS launchd fallback、
    README.md 12節）とは別物であることを明示する — ハードコードされたローカル
    パスを持たない。"""
    text = _script_text()
    assert "/Users/" not in text
    assert ".venv/bin" not in text
