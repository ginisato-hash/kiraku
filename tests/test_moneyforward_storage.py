"""Financial-state persistence: privacy gates, corruption and partial failure."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
import requests

from yuge_finance import moneyforward_storage as storage


class FakeResponse:
    def __init__(self, body: bytes = b"", status: int = 200):
        self.body = body
        self.status_code = status
        self.closed = False

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def envelope(result, *, success=True):
    return FakeResponse(json.dumps({"success": success, "result": result}).encode())


def private_replies():
    return [envelope({"enabled": False}), envelope({"domains": []})]


class MemoryR2:
    def __init__(self):
        self.objects = {}
        self.events = []
        self.fail = None
        self.ambiguous_summary_failure = False

    def assert_private(self):
        self.events.append(("privacy",))

    def get_object(self, key, *, allow_missing=False):
        self.events.append(("get", key))
        if key not in self.objects:
            if allow_missing:
                return None
            raise storage.MoneyForwardStorageError("MF_STORAGE_HTTP_REJECTED")
        return self.objects[key]

    def put_object(self, key, body, *, content_type):
        self.events.append(("put", key))
        category = "summary" if key == storage.SUMMARY_KEY else "state"
        if self.fail == category:
            raise storage.MoneyForwardStorageError("MF_STORAGE_TRANSPORT_FAILED")
        self.objects[key] = body
        if category == "summary" and self.ambiguous_summary_failure:
            raise storage.MoneyForwardStorageError("MF_STORAGE_TRANSPORT_FAILED")


def make_local_state(path: Path, value="synthetic-old", *, wal=False):
    path.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path / "ledger.sqlite")
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    connection.execute("INSERT INTO records VALUES (?)", (value,))
    connection.commit()
    (path / "summary.json").write_text(
        json.dumps({"status": "ok", "attempted_at": value, "accounts": []}),
        encoding="utf-8",
    )
    if wal:
        return connection
    connection.close()


def record_value(path: Path):
    with sqlite3.connect(path / "ledger.sqlite") as connection:
        return connection.execute("SELECT value FROM records").fetchone()[0]


@pytest.mark.parametrize("managed", [{"enabled": True}, {}, {"enabled": "false"}, {"enabled": 0}])
def test_no_object_access_until_managed_domain_is_explicitly_disabled(tmp_path, managed):
    session = FakeSession([envelope(managed)])
    client = storage.PrivateR2Client("a" * 32, "test-token", session=session)
    with pytest.raises(storage.MoneyForwardStorageError):
        storage.restore(tmp_path / "state", client=client)
    assert all("/objects/" not in call[1] for call in session.calls)
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("domains", [[{"domain": "example.invalid", "enabled": False}], None, {}])
def test_no_object_access_when_custom_domain_contract_is_not_empty(tmp_path, domains):
    session = FakeSession([envelope({"enabled": False}), envelope({"domains": domains})])
    client = storage.PrivateR2Client("a" * 32, "test-token", session=session)
    with pytest.raises(storage.MoneyForwardStorageError):
        storage.restore(tmp_path / "state", client=client)
    assert all("/objects/" not in call[1] for call in session.calls)


@pytest.mark.parametrize("reply", [FakeResponse(b"not-json"), envelope({}, success=False), FakeResponse(status=404)])
def test_unreadable_privacy_configuration_is_never_initial_state(tmp_path, reply):
    session = FakeSession([reply])
    client = storage.PrivateR2Client("a" * 32, "test-token", session=session)
    with pytest.raises(storage.MoneyForwardStorageError):
        storage.restore(tmp_path / "state", client=client)
    assert len(session.calls) == 1


def test_only_summary_404_is_an_initial_run(tmp_path):
    session = FakeSession(private_replies() + [FakeResponse(status=404)])
    client = storage.PrivateR2Client("a" * 32, "test-token", session=session)
    target = tmp_path / "state"
    assert storage.restore(target, client=client) is False
    assert list(target.iterdir()) == []
    assert target.stat().st_mode & 0o777 == 0o700


def test_ensure_bucket_only_creates_fixed_name_after_metadata_404():
    session = FakeSession([
        FakeResponse(status=404), envelope({"name": storage.PRIVATE_BUCKET}),
        *private_replies(),
    ])
    client = storage.PrivateR2Client("a" * 32, "test-token", session=session)
    client.ensure_private_bucket()
    assert [call[0] for call in session.calls] == ["GET", "POST", "GET", "GET"]
    creation = session.calls[1]
    assert creation[1].endswith("/r2/buckets")
    assert json.loads(creation[2]["data"]) == {"name": "kiraku-finance-private"}
    assert all("/objects/" not in call[1] for call in session.calls)


def test_ensure_existing_bucket_performs_reads_only():
    session = FakeSession([envelope({"name": storage.PRIVATE_BUCKET}), *private_replies()])
    client = storage.PrivateR2Client("a" * 32, "test-token", session=session)
    client.ensure_private_bucket()
    assert all(call[0] == "GET" for call in session.calls)


@pytest.mark.parametrize("response", [FakeResponse(status=403), envelope({}, success=False), FakeResponse(b"invalid")])
def test_ensure_bucket_never_creates_after_uncertain_existence(response):
    session = FakeSession([response])
    client = storage.PrivateR2Client("a" * 32, "test-token", session=session)
    with pytest.raises(storage.MoneyForwardStorageError):
        client.ensure_private_bucket()
    assert len(session.calls) == 1
    assert session.calls[0][0] == "GET"


def test_ensure_public_bucket_does_not_change_settings():
    session = FakeSession([envelope({"name": storage.PRIVATE_BUCKET}), envelope({"enabled": True})])
    client = storage.PrivateR2Client("a" * 32, "test-token", session=session)
    with pytest.raises(storage.MoneyForwardStorageError):
        client.ensure_private_bucket()
    assert all(call[0] == "GET" for call in session.calls)


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
def test_transport_auth_and_redirect_failures_do_not_erase_state(tmp_path, monkeypatch, status):
    monkeypatch.setattr(storage.time, "sleep", lambda _seconds: None)
    session = FakeSession(private_replies() + [FakeResponse(b"secret-response", status)] * 3)
    client = storage.PrivateR2Client("a" * 32, "secret-token", session=session)
    with pytest.raises(storage.MoneyForwardStorageError) as error:
        storage.restore(tmp_path / "state", client=client)
    assert "secret" not in str(error.value)
    assert not (tmp_path / "state").exists()
    assert all(call[2]["allow_redirects"] is False for call in session.calls)


def test_request_exception_is_sanitized_and_bounded(monkeypatch):
    monkeypatch.setattr(storage.time, "sleep", lambda _seconds: None)
    session = FakeSession([requests.Timeout("private-token-and-account")] * 3)
    client = storage.PrivateR2Client("a" * 32, "test-token", session=session)
    with pytest.raises(storage.MoneyForwardStorageError, match="^MF_STORAGE_TRANSPORT_FAILED$"):
        client.assert_private()
    assert len(session.calls) == 3


def test_private_generation_round_trip_and_permissions(tmp_path):
    source, restored = tmp_path / "source", tmp_path / "restored"
    make_local_state(source)
    client = MemoryR2()
    storage.publish(source, client=client)
    remote_summary = json.loads(client.objects[storage.SUMMARY_KEY])
    pointer = remote_summary["storage"]
    assert storage.STATE_KEY_RE.fullmatch(pointer["state_key"])
    assert hashlib.sha256(client.objects[pointer["state_key"]]).hexdigest() == pointer["state_sha256"]
    assert client.events[0] == ("privacy",)
    assert client.events[-1] == ("put", storage.SUMMARY_KEY)
    assert storage.restore(restored, client=client) is True
    assert record_value(restored) == "synthetic-old"
    assert (restored / "summary.json").read_bytes() == client.objects[storage.SUMMARY_KEY]
    assert (restored / "ledger.sqlite").stat().st_mode & 0o777 == 0o600
    assert (restored / "summary.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure", ["state", "summary"])
def test_partial_publish_keeps_previous_committed_pair_readable(tmp_path, failure):
    client = MemoryR2()
    old, new = tmp_path / "old", tmp_path / "new"
    make_local_state(old)
    storage.publish(old, client=client)
    old_marker = client.objects[storage.SUMMARY_KEY]
    make_local_state(new, "synthetic-new")
    client.fail = failure
    with pytest.raises(storage.MoneyForwardStorageError):
        storage.publish(new, client=client)
    assert client.objects[storage.SUMMARY_KEY] == old_marker
    client.fail = None
    restored = tmp_path / "restore"
    assert storage.restore(restored, client=client)
    assert record_value(restored) == "synthetic-old"


def test_commit_ack_timeout_still_leaves_a_consistent_generation(tmp_path):
    client = MemoryR2()
    source = tmp_path / "source"
    make_local_state(source, "new-generation")
    client.ambiguous_summary_failure = True
    with pytest.raises(storage.MoneyForwardStorageError):
        storage.publish(source, client=client)
    assert storage.restore(tmp_path / "restore", client=client)
    assert record_value(tmp_path / "restore") == "new-generation"


def test_sqlite_snapshot_includes_committed_wal_pages(tmp_path):
    source = tmp_path / "source"
    connection = make_local_state(source, "committed-in-wal", wal=True)
    try:
        assert (source / "ledger.sqlite-wal").stat().st_size > 0
        client = MemoryR2()
        storage.publish(source, client=client)
        storage.restore(tmp_path / "restore", client=client)
        assert record_value(tmp_path / "restore") == "committed-in-wal"
    finally:
        connection.close()


@pytest.mark.parametrize("corruption", ["hash", "missing", "invalid_sqlite", "pointer", "summary"])
def test_corrupt_generation_fails_closed_without_exposing_local_pair(tmp_path, corruption):
    client = MemoryR2()
    source = tmp_path / "source"
    make_local_state(source)
    storage.publish(source, client=client)
    marker = json.loads(client.objects[storage.SUMMARY_KEY])
    state_key = marker["storage"]["state_key"]
    if corruption == "hash":
        client.objects[state_key] += b"corruption"
    elif corruption == "missing":
        del client.objects[state_key]
    elif corruption == "invalid_sqlite":
        invalid = b"not-a-database"
        client.objects[state_key] = invalid
        marker["storage"]["state_sha256"] = hashlib.sha256(invalid).hexdigest()
        client.objects[storage.SUMMARY_KEY] = json.dumps(marker).encode()
    elif corruption == "pointer":
        marker["storage"]["state_key"] = "../../another-bucket/secret"
        client.objects[storage.SUMMARY_KEY] = json.dumps(marker).encode()
    else:
        client.objects[storage.SUMMARY_KEY] = b"[]"
    destination = tmp_path / "restore"
    with pytest.raises(storage.MoneyForwardStorageError):
        storage.restore(destination, client=client)
    assert not (destination / "ledger.sqlite").exists()
    assert not (destination / "summary.json").exists()


def test_restore_does_not_overwrite_existing_local_ledger(tmp_path):
    make_local_state(tmp_path / "state")
    original = (tmp_path / "state" / "ledger.sqlite").read_bytes()
    client = MemoryR2()
    with pytest.raises(storage.MoneyForwardStorageError, match="MF_STORAGE_LOCAL_STATE_EXISTS"):
        storage.restore(tmp_path / "state", client=client)
    assert (tmp_path / "state" / "ledger.sqlite").read_bytes() == original
    assert client.events == [("privacy",)]


def test_publish_refuses_invalid_sqlite_before_any_upload(tmp_path):
    make_local_state(tmp_path / "state")
    (tmp_path / "state" / "ledger.sqlite").write_bytes(b"not-sqlite")
    client = MemoryR2()
    with pytest.raises(storage.MoneyForwardStorageError):
        storage.publish(tmp_path / "state", client=client)
    assert client.events == [("privacy",)]


def test_rest_upload_uses_raw_body_fixed_bucket_and_literal_key_slashes():
    session = FakeSession([envelope({})])
    client = storage.PrivateR2Client("a" * 32, "test-token", session=session)
    key = "bank-sync/state/" + "b" * 32 + ".sqlite"
    client.put_object(key, b"synthetic-payload", content_type="application/octet-stream")
    method, url, kwargs = session.calls[0]
    assert method == "PUT"
    assert "/buckets/kiraku-finance-private/objects/bank-sync/state/" in url
    assert "%2F" not in url
    assert kwargs["data"] == b"synthetic-payload"
    assert kwargs["headers"]["Content-Type"] == "application/octet-stream"


def test_cli_sanitizes_unexpected_exception_and_never_prints_payload(monkeypatch, capsys):
    def fail(_path):
        raise OSError("synthetic-secret-bank-account-and-amount")
    monkeypatch.setattr(storage, "publish", fail)
    assert storage.main(["publish"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "MF_STORAGE_FAILED\n"


def test_cli_names_missing_credential_without_values(monkeypatch, capsys):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "synthetic-secret")
    assert storage.main(["restore"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "MF_STORAGE_MISSING_CLOUDFLARE_ACCOUNT_ID\n"
    assert "synthetic-secret" not in captured.out + captured.err
