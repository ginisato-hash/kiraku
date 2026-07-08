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


def _month_snapshot(month):
    return json.dumps({
        "target_month": month,
        "beds24_revenue_net_for_bi": 500000,
        "cash_operating_breakeven_revenue": 2000000,
        "booking_pace_status": "green",
    })


def _seed_with_months(dir_, months=("2026-07", "2026-08")):
    _seed(dir_)
    manifest = json.loads((dir_ / "manifest.json").read_text(encoding="utf-8"))
    manifest["available_months"] = list(months)
    manifest["default_month"] = months[0]
    (dir_ / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for m in months:
        month_dir = dir_ / "months" / m
        month_dir.mkdir(parents=True, exist_ok=True)
        (month_dir / "bi_snapshot.json").write_text(_month_snapshot(m), encoding="utf-8")
        (month_dir / "bi_daily_timeseries.csv").write_text(f"date\n{m}-01\n", encoding="utf-8")
        (month_dir / "bi_monthly_kpi.csv").write_text(f"month\n{m}\n", encoding="utf-8")
        (month_dir / "bi_validation_status.json").write_text('{"all_ok": true}', encoding="utf-8")
        (month_dir / "bi_exception_summary.json").write_text('{"total": 0}', encoding="utf-8")


def test_month_snapshots_included_in_upload_targets(tmp_path):
    _seed_with_months(tmp_path)
    res = publish_r2.publish(source_dir=tmp_path, dry_run=True)
    for m in ("2026-07", "2026-08"):
        for fn in publish_r2.MONTH_UPLOAD_FILENAMES:
            assert f"latest/months/{m}/{fn}" in res["would_upload_keys"]


def test_month_validation_fails_when_month_snapshot_missing(tmp_path):
    _seed_with_months(tmp_path)
    (tmp_path / "months" / "2026-08" / "bi_snapshot.json").unlink()
    issues = publish_r2.validate(tmp_path)
    assert any("2026-08" in i and "bi_snapshot.json" in i for i in issues)


def test_month_validation_fails_when_required_field_missing(tmp_path):
    _seed_with_months(tmp_path)
    snap_path = tmp_path / "months" / "2026-07" / "bi_snapshot.json"
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    del snap["beds24_revenue_net_for_bi"]
    snap_path.write_text(json.dumps(snap), encoding="utf-8")
    issues = publish_r2.validate(tmp_path)
    assert any("beds24_revenue_net_for_bi" in i for i in issues)


def test_month_validation_skipped_when_manifest_has_no_available_months(tmp_path):
    """本機能導入前に生成されたmanifest(available_monthsが無い)は月別検証をスキップする。"""
    _seed(tmp_path)
    issues = publish_r2.validate(tmp_path)
    assert issues == []


def test_publish_does_not_touch_local_bi_on_failure(tmp_path):
    _seed(tmp_path)
    (tmp_path / "bi_snapshot.json").write_text("{ broken", encoding="utf-8")
    before = (tmp_path / "bi_daily_timeseries.csv").read_bytes()
    with pytest.raises(publish_r2.PublishR2Error):
        publish_r2.publish(source_dir=tmp_path, dry_run=False)
    # ローカルBIファイルは書き換えられていない
    assert (tmp_path / "bi_daily_timeseries.csv").read_bytes() == before


# ---------------- --preserve-bank-fields-from-r2 (sticky bank field merge) ----------------
class _FakeWrangler:
    """_put()をモック化し、実際のwrangler呼び出しをせず成功扱いにする。"""

    def __init__(self):
        self.calls = []

    def __call__(self, bucket, prefix, relative_key, file_path, cwd):
        self.calls.append(relative_key)
        return {"key": f"{prefix}/{relative_key}", "ok": True}


def test_preserve_bank_fields_option_exists_in_cli():
    from yuge_finance.cli import build_parser
    args = build_parser().parse_args(["publish-bi-r2", "--preserve-bank-fields-from-r2"])
    assert args.preserve_bank_fields_from_r2 is True


def test_preserve_bank_fields_merges_root_and_month_snapshots(tmp_path, monkeypatch):
    _seed_with_months(tmp_path)
    previous = {
        "generated_at_jst": "2026-07-09T07:00:00+09:00",
        "bank_actual_latest_balance": 3052421.0,
        "bank_csv_import_status": "imported",
        "bank_csv_imported_rows": 89,
    }
    monkeypatch.setattr(publish_r2, "_fetch_public_snapshot", lambda month=None, timeout=20: previous)
    monkeypatch.setattr(publish_r2, "_wrangler_available", lambda: True)
    fake_put = _FakeWrangler()
    monkeypatch.setattr(publish_r2, "_put", fake_put)

    res = publish_r2.publish(source_dir=tmp_path, dry_run=False, preserve_bank_fields_from_r2=True)

    assert res["bank_fields_sources"]["previous_r2_snapshot"] == 3  # root + 2 months
    root_snap = json.loads((tmp_path / "bi_snapshot.json").read_text(encoding="utf-8"))
    assert root_snap["bank_actual_latest_balance"] == 3052421.0
    assert root_snap["bank_fields_source"] == "previous_r2_snapshot"
    month_snap = json.loads((tmp_path / "months" / "2026-07" / "bi_snapshot.json").read_text(encoding="utf-8"))
    assert month_snap["bank_actual_latest_balance"] == 3052421.0
    # 非bank系フィールドは今回値のまま
    assert month_snap["target_month"] == "2026-07"
    assert month_snap["booking_pace_status"] == "green"


def test_preserve_bank_fields_does_not_reduce_upload_target_count(tmp_path, monkeypatch):
    _seed_with_months(tmp_path)
    monkeypatch.setattr(publish_r2, "_fetch_public_snapshot", lambda month=None, timeout=20: None)
    monkeypatch.setattr(publish_r2, "_wrangler_available", lambda: True)
    fake_put = _FakeWrangler()
    monkeypatch.setattr(publish_r2, "_put", fake_put)

    res_without = publish_r2.publish(source_dir=tmp_path, dry_run=True)
    res_with = publish_r2.publish(source_dir=tmp_path, dry_run=False, preserve_bank_fields_from_r2=True)
    assert res_with["uploaded_count"] == len(res_without["would_upload_keys"])


def test_preserve_bank_fields_keeps_current_import_when_new_snapshot_has_bank_data(tmp_path, monkeypatch):
    _seed(tmp_path)
    snap_path = tmp_path / "bi_snapshot.json"
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    snap.update({"bank_actual_latest_balance": 111.0, "bank_csv_import_status": "imported",
                "bank_csv_imported_rows": 3})
    snap_path.write_text(json.dumps(snap), encoding="utf-8")

    previous = {"bank_actual_latest_balance": 999.0, "bank_csv_import_status": "imported",
               "bank_csv_imported_rows": 99}
    monkeypatch.setattr(publish_r2, "_fetch_public_snapshot", lambda month=None, timeout=20: previous)
    monkeypatch.setattr(publish_r2, "_wrangler_available", lambda: True)
    monkeypatch.setattr(publish_r2, "_put", _FakeWrangler())

    res = publish_r2.publish(source_dir=tmp_path, dry_run=False, preserve_bank_fields_from_r2=True)
    assert res["bank_fields_sources"]["current_import"] == 1
    merged = json.loads(snap_path.read_text(encoding="utf-8"))
    assert merged["bank_actual_latest_balance"] == 111.0  # 今回値のまま


def test_dry_run_never_calls_fetch_or_rewrites_files(tmp_path, monkeypatch):
    _seed(tmp_path)
    before = (tmp_path / "bi_snapshot.json").read_bytes()
    called = []
    monkeypatch.setattr(publish_r2, "_fetch_public_snapshot",
                        lambda month=None, timeout=20: called.append(month) or None)
    publish_r2.publish(source_dir=tmp_path, dry_run=True, preserve_bank_fields_from_r2=True)
    assert called == []
    assert (tmp_path / "bi_snapshot.json").read_bytes() == before
