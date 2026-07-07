"""launchdは今回変更しない／画面のprimary描画がdeprecated fieldを直接参照しないことのgrepチェック。"""
from pathlib import Path

from yuge_finance import config

DEPRECATED_FIELDS = ["breakeven_revenue_current_structure", "revenue_reconciliation_difference"]


def test_launchd_plist_does_not_reference_publish_r2():
    """--publish-r2 はまだlaunchdに組み込まない（Phase 9）。"""
    plist = config.ROOT / "launchd" / "com.yuge.kiraku.beds24-bi-refresh.plist.template"
    text = plist.read_text(encoding="utf-8")
    assert "--publish-r2" not in text
    assert "--publish" in text  # 既存の --publish は維持


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
    end = text.index("function buildDetails")
    body = text[start:end]
    for field in DEPRECATED_FIELDS:
        assert field not in body, f"buildPrimaryCards が deprecated field を使っている: {field}"


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
