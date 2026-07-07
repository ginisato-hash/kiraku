"""refresh-beds24-bi の --publish-r2 オプション（CLI引数のみのテスト。R2へは接続しない）。"""
from yuge_finance import cli


def test_refresh_beds24_bi_has_publish_r2_option():
    parser = cli.build_parser()
    args = parser.parse_args(["refresh-beds24-bi", "--month", "2026-07", "--publish-r2"])
    assert args.publish_r2 is True
    assert args.no_publish_r2 is False


def test_refresh_beds24_bi_publish_r2_defaults_to_false():
    parser = cli.build_parser()
    args = parser.parse_args(["refresh-beds24-bi", "--month", "2026-07"])
    assert args.publish_r2 is False


def test_publish_bi_r2_cli_exists_with_expected_options():
    parser = cli.build_parser()
    args = parser.parse_args(["publish-bi-r2", "--dry-run", "--bucket", "kiraku-bi-data",
                              "--prefix", "latest"])
    assert args.dry_run is True
    assert args.bucket == "kiraku-bi-data"
    assert args.prefix == "latest"


def test_without_publish_r2_flag_r2_upload_not_called(tmp_path, monkeypatch):
    """--publish-r2 を渡さない限り publish_r2.publish() が呼ばれないこと。"""
    from yuge_finance import bi_refresh, config, db, locks

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "ROOT", tmp_path)

    def fake_fetch(month, conn):
        return 0

    monkeypatch.setattr(bi_refresh, "_fetch_beds24", fake_fetch)
    db.connect(tmp_path / "t.sqlite").close()

    called = {"r2": False, "local": False}
    monkeypatch.setattr(cli.publish_r2, "publish", lambda *a, **k: called.update(r2=True))
    monkeypatch.setattr(cli.publish, "publish", lambda *a, **k: called.update(local=True))

    parser = cli.build_parser()
    args = parser.parse_args(["refresh-beds24-bi", "--month", "2026-07", "--months", "1"])
    cli.cmd_refresh_beds24_bi(args)

    assert called["r2"] is False   # --publish-r2 を渡していないのでR2アップロードは呼ばれない
    assert called["local"] is False  # --publish も渡していない


def test_with_publish_r2_flag_calls_publish_r2(tmp_path, monkeypatch):
    from yuge_finance import bi_refresh, config, db

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(config, "ROOT", tmp_path)

    def fake_fetch(month, conn):
        return 0

    monkeypatch.setattr(bi_refresh, "_fetch_beds24", fake_fetch)
    db.connect(tmp_path / "t.sqlite").close()

    called = {"r2": False}
    monkeypatch.setattr(cli.publish_r2, "publish", lambda *a, **k: called.update(r2=True) or {})

    parser = cli.build_parser()
    args = parser.parse_args(["refresh-beds24-bi", "--month", "2026-07", "--months", "1",
                              "--publish-r2"])
    cli.cmd_refresh_beds24_bi(args)

    assert called["r2"] is True
