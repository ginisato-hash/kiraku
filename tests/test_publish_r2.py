"""publish-bi-r2（R2直接アップロード。Worker本体はdeployしない）。"""
import json

import pytest

from yuge_finance import publish_r2


def _seed(dir_):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "manifest.json").write_text(json.dumps({"generated_at_jst": "2026-07-08T00:00:00+09:00"}),
                                        encoding="utf-8")
    (dir_ / "bi_snapshot.json").write_text(json.dumps({
        "generated_at_jst": "2026-07-08T00:00:00+09:00",
        "cash_operating_breakeven_revenue": 2000000,
        "cash_operating_breakeven_achievement_rate": 0.5,
        "booking_pace_status": "yellow",
        "month_elapsed_rate": 0.26,
        "projected_month_end_bep_achievement_rate": 0.5,
    }), encoding="utf-8")
    (dir_ / "bi_daily_timeseries.csv").write_text("date\n2026-07-01\n", encoding="utf-8")
    (dir_ / "bi_monthly_kpi.csv").write_text("month\n2026-07\n", encoding="utf-8")
    (dir_ / "bi_validation_status.json").write_text('{"all_ok": true}', encoding="utf-8")
    (dir_ / "bi_exception_summary.json").write_text('{"total": 0}', encoding="utf-8")


def test_dry_run_lists_target_files(tmp_path):
    _seed(tmp_path)
    res = publish_r2.publish(source_dir=tmp_path, dry_run=True)
    assert res["dry_run"] is True
    assert res["bucket"] == "kiraku-bi-data"
    assert res["prefix"] == "latest"
    assert res["would_upload_keys"] == [f"latest/{fn}" for fn in publish_r2.UPLOAD_FILES]


def test_upload_keys_fixed_to_latest_prefix(tmp_path):
    _seed(tmp_path)
    res = publish_r2.publish(source_dir=tmp_path, dry_run=True)
    for key in res["would_upload_keys"]:
        assert key.startswith("latest/")


def test_json_validation_fails_on_broken_manifest(tmp_path):
    _seed(tmp_path)
    (tmp_path / "manifest.json").write_text("{ broken", encoding="utf-8")
    with pytest.raises(publish_r2.PublishR2Error):
        publish_r2.publish(source_dir=tmp_path, dry_run=True)


def test_validation_fails_without_cash_bep_field(tmp_path):
    _seed(tmp_path)
    snap = json.loads((tmp_path / "bi_snapshot.json").read_text(encoding="utf-8"))
    del snap["cash_operating_breakeven_revenue"]
    (tmp_path / "bi_snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
    with pytest.raises(publish_r2.PublishR2Error, match="cash_operating_breakeven_revenue"):
        publish_r2.publish(source_dir=tmp_path, dry_run=True)


def test_validation_fails_without_booking_pace_fields(tmp_path):
    _seed(tmp_path)
    snap = json.loads((tmp_path / "bi_snapshot.json").read_text(encoding="utf-8"))
    del snap["booking_pace_status"]
    del snap["month_elapsed_rate"]
    (tmp_path / "bi_snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
    issues = publish_r2.validate(tmp_path)
    assert any("booking_pace_status" in i for i in issues)
    assert any("month_elapsed_rate" in i for i in issues)


def test_validation_fails_without_projected_bep_field(tmp_path):
    _seed(tmp_path)
    snap = json.loads((tmp_path / "bi_snapshot.json").read_text(encoding="utf-8"))
    del snap["projected_month_end_bep_achievement_rate"]
    (tmp_path / "bi_snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
    issues = publish_r2.validate(tmp_path)
    assert any("projected_month_end_bep_achievement_rate" in i for i in issues)


def test_missing_manifest_fails_validation(tmp_path):
    _seed(tmp_path)
    (tmp_path / "manifest.json").unlink()
    issues = publish_r2.validate(tmp_path)
    assert any("manifest.json" in i for i in issues)


def test_bank_report_files_are_optional_when_absent(tmp_path):
    """本機能導入前に生成されたBI出力(銀行レポート無し)でもpublish-bi-r2は失敗しない。"""
    _seed(tmp_path)
    res = publish_r2.publish(source_dir=tmp_path, dry_run=True)
    for fn in publish_r2.OPTIONAL_UPLOAD_FILES:
        assert f"latest/{fn}" not in res["would_upload_keys"]


def test_bank_report_files_are_uploaded_when_present(tmp_path):
    _seed(tmp_path)
    (tmp_path / "bank_cashflow_summary.json").write_text('{"month": "2026-07"}', encoding="utf-8")
    res = publish_r2.publish(source_dir=tmp_path, dry_run=True)
    assert "latest/bank_cashflow_summary.json" in res["would_upload_keys"]
    assert "latest/bank_cost_model_candidates.json" not in res["would_upload_keys"]


def test_publish_does_not_touch_local_bi_on_failure(tmp_path):
    _seed(tmp_path)
    (tmp_path / "bi_snapshot.json").write_text("{ broken", encoding="utf-8")
    before = (tmp_path / "bi_daily_timeseries.csv").read_bytes()
    with pytest.raises(publish_r2.PublishR2Error):
        publish_r2.publish(source_dir=tmp_path, dry_run=False)
    # ローカルBIファイルは書き換えられていない
    assert (tmp_path / "bi_daily_timeseries.csv").read_bytes() == before
