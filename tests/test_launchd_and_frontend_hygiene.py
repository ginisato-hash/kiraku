"""launchd 15分自動更新（refresh-beds24-bi + publish-bi-r2）／
画面のprimary描画がdeprecated fieldを直接参照しないことのgrepチェック。"""
import os
from pathlib import Path

from yuge_finance import config

DEPRECATED_FIELDS = ["breakeven_revenue_current_structure", "revenue_reconciliation_difference"]

WRAPPER_SCRIPT = config.ROOT / "scripts" / "refresh_beds24_bi_and_publish_r2.sh"
LAUNCHD_PLIST = config.ROOT / "launchd" / "com.yuge.kiraku.beds24-bi-refresh.plist.template"


def test_launchd_plist_calls_wrapper_script_not_raw_cli_flags():
    """15分更新の実処理は wrapper script に集約する（plistは直接CLIフラグを持たない）。"""
    text = LAUNCHD_PLIST.read_text(encoding="utf-8")
    assert "scripts/refresh_beds24_bi_and_publish_r2.sh" in text
    assert "--publish-r2" not in text  # フラグとしては使わない（wrapper内でpublish-bi-r2を別実行する）


def test_launchd_plist_never_calls_wrangler_deploy_or_close_month():
    text = LAUNCHD_PLIST.read_text(encoding="utf-8")
    assert "wrangler deploy" not in text
    assert "close-month" not in text


def test_launchd_plist_start_interval_is_900_seconds():
    text = LAUNCHD_PLIST.read_text(encoding="utf-8")
    assert "<integer>900</integer>" in text


def test_launchd_plist_uses_official_path_not_documents():
    text = LAUNCHD_PLIST.read_text(encoding="utf-8")
    assert "/Users/ginisato/YugeFinance/kiraku-finance-automation" in text
    assert "Documents/YugeFinance" not in text


def test_refresh_wrapper_script_exists_and_is_executable():
    assert WRAPPER_SCRIPT.exists()
    assert os.access(WRAPPER_SCRIPT, os.X_OK)


def test_refresh_wrapper_script_runs_auto_discovery_refresh_and_r2_publish():
    text = WRAPPER_SCRIPT.read_text(encoding="utf-8")
    assert "refresh-beds24-bi --auto-months-with-bookings --publish" in text
    assert "publish-bi-r2" in text


def test_refresh_wrapper_script_never_deploys_or_touches_ledger():
    """15分更新は仕訳/PL/BS/CF/Excelを触らない。wrangler deploy・close-monthも含めない。"""
    text = WRAPPER_SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("wrangler deploy", "close-month", "build-ledger", "export-excel"):
        assert forbidden not in text


def test_refresh_wrapper_script_checks_manifest_after_publish():
    """R2 publish後にmanifestのgenerated_at_jstを確認するログを出す（日付跨ぎ不具合対応）。"""
    text = WRAPPER_SCRIPT.read_text(encoding="utf-8")
    assert "curl" in text
    assert "/api/manifest" in text
    assert "generated_at_jst" in text
    publish_idx = text.index("publish-bi-r2")
    curl_idx = text.index("curl")
    assert curl_idx > publish_idx, "manifest確認curlはpublish-bi-r2の後に実行すること"


def test_refresh_wrapper_script_sets_path_for_launchd():
    """launchdの最小PATHではnpx/wranglerが見つからないため、明示的にPATHを通す。"""
    text = WRAPPER_SCRIPT.read_text(encoding="utf-8")
    assert "/usr/local/bin" in text
    assert "PATH=" in text


def test_app_js_does_not_reference_deprecated_fields_directly():
    """app.js は生のsnapshotフィールドを直接大量参照せず、biViewModel経由で描画する。"""
    app_js = config.ROOT / "cloudflare" / "bi-web" / "public" / "app.js"
    text = app_js.read_text(encoding="utf-8")
    for field in DEPRECATED_FIELDS:
        assert field not in text, f"app.js が deprecated field を直接参照している: {field}"


def test_bi_view_model_primary_cards_do_not_use_deprecated_fields():
    """biViewModel.js の primaryCards 生成部が deprecated field を使っていないこと。"""
    vm_js = config.ROOT / "cloudflare" / "bi-web" / "public" / "biViewModel.js"
    text = vm_js.read_text(encoding="utf-8")
    # buildPrimaryCards関数の本体のみを対象に、deprecated fieldへの参照が無いか確認
    start = text.index("function buildPrimaryCards")
    end = text.index("function buildDetailSections")
    body = text[start:end]
    for field in DEPRECATED_FIELDS:
        assert field not in body, f"buildPrimaryCards が deprecated field を使っている: {field}"


def test_components_js_does_not_reference_deprecated_fields():
    """components.js（DOM生成）も生のdeprecated fieldを直接参照しないこと。"""
    comp_js = config.ROOT / "cloudflare" / "bi-web" / "public" / "components.js"
    text = comp_js.read_text(encoding="utf-8")
    for field in DEPRECATED_FIELDS:
        assert field not in text, f"components.js が deprecated field を直接参照している: {field}"


def test_css_is_separated_from_index_html():
    """index.htmlから肥大化したインラインCSSが分離され、styles.cssへ切り出されていること。"""
    index_html = config.ROOT / "cloudflare" / "bi-web" / "public" / "index.html"
    styles_css = config.ROOT / "cloudflare" / "bi-web" / "public" / "styles.css"
    assert styles_css.exists()
    html_text = index_html.read_text(encoding="utf-8")
    assert "<style>" not in html_text
    assert 'rel="stylesheet" href="./styles.css"' in html_text
    # デザイントークン(CSS variables)がstyles.cssに定義されていること
    css_text = styles_css.read_text(encoding="utf-8")
    for token in ["--bg", "--surface", "--green", "--amber", "--red", "--radius-lg"]:
        assert token in css_text


FORBIDDEN_COUPON_WORDING = ["クーポン加算", "coupon revenue", "beds24_coupon_revenue_included"]


def test_ui_files_do_not_use_coupon_as_addition_wording():
    """coupon は直割引扱い。UI表示で「クーポン加算」等の誤表記が使われていないこと。
    beds24_coupon_revenue_included はdeprecated fieldとしてJSONには残るが、画面表示では使わない。
    """
    bi_web = config.ROOT / "cloudflare" / "bi-web" / "public"
    for filename in ["biViewModel.js", "components.js", "app.js", "index.html"]:
        text = (bi_web / filename).read_text(encoding="utf-8")
        for phrase in FORBIDDEN_COUPON_WORDING:
            assert phrase not in text, f"{filename} に禁止文言が残っている: {phrase}"


def test_bi_snapshot_json_still_retains_deprecated_fields_for_debug_csv_export(tmp_path):
    """bi_snapshot.jsonからは削除しない（将来の分析・デバッグ・CSV exportのため）。"""
    from yuge_finance import db, monthly
    conn = db.connect(tmp_path / "t.sqlite")
    ctx = monthly.assemble("2026-06", conn)
    bm = ctx["breakeven_model"]
    rr = ctx["revenue_recon"]
    # breakeven_revenue_current_structure は後方互換フィールドとしてJSONに残る（画面のprimaryには使わない）
    assert "breakeven_revenue_current_structure" in bm
    # 同月比較の差額はlegacy参考値として残る（判定には使わない）
    assert "revenue_reconciliation_difference_same_month" in rr["legacy_same_month_reference"]
    conn.close()
