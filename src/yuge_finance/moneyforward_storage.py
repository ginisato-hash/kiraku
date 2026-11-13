"""Private R2 persistence for the Money Forward read-only bank ledger.

The single writer uploads an immutable SQLite snapshot first, then commits it by
replacing ``bank-sync/latest/summary.json``.  The summary contains the snapshot
key and SHA-256; a failed upload can leave an orphan but never a mixed generation.
Restore only follows this pointer.  This module never uses the public BI bucket.

Cloudflare REST contracts (checked 2026-11-13):
https://developers.cloudflare.com/api/resources/r2/subresources/buckets/subresources/objects/methods/get/
https://developers.cloudflare.com/api/resources/r2/subresources/buckets/subresources/objects/methods/upload/
https://developers.cloudflare.com/api/resources/r2/subresources/buckets/subresources/domains/subresources/managed/methods/list/
https://developers.cloudflare.com/api/resources/r2/subresources/buckets/subresources/domains/subresources/custom/methods/list/
https://developers.cloudflare.com/api/resources/r2/subresources/buckets/methods/get/
https://developers.cloudflare.com/api/resources/r2/subresources/buckets/methods/create/

REST object uploads are limited to 300 MB.  This helper fails before that limit;
moving a larger ledger to S3/multipart is an explicit future migration.  The
workflow's dedicated concurrency group is the single-writer lock.  The explicit
ensure-bucket command may create this one bucket after a metadata 404.  It never
changes public access, domains, or historical objects, and never deletes a bucket.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
from typing import Any
from urllib.parse import quote
import uuid

import requests


PRIVATE_BUCKET = "kiraku-finance-private"
SUMMARY_KEY = "bank-sync/latest/summary.json"
DEFAULT_STATE_DIR = Path("data/private/moneyforward")
MAX_STATE_BYTES = 290_000_000
MAX_SUMMARY_BYTES = 2_000_000
MAX_API_RESPONSE_BYTES = 100_000
STATE_KEY_RE = re.compile(r"bank-sync/state/[0-9a-f]{32}\.sqlite\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class MoneyForwardStorageError(RuntimeError):
    """Only a fixed reason code is safe to display; never attach a response."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _decode_json(raw: bytes, code: str) -> dict[str, Any]:
    def reject_constant(_value: str) -> None:
        raise ValueError

    try:
        value = json.loads(raw, parse_constant=reject_constant)
    except (ValueError, UnicodeError):
        raise MoneyForwardStorageError(code) from None
    if not isinstance(value, dict):
        raise MoneyForwardStorageError(code)
    return value


class PrivateR2Client:
    """A fixed-bucket REST client using the existing Cloudflare credentials."""

    def __init__(self, account_id: str, api_token: str, *, session=None):
        if not re.fullmatch(r"[0-9a-fA-F]{32}", account_id):
            raise MoneyForwardStorageError("MF_STORAGE_ACCOUNT_CONFIG_INVALID")
        if not api_token or api_token != api_token.strip() or "\n" in api_token or "\r" in api_token:
            raise MoneyForwardStorageError("MF_STORAGE_TOKEN_CONFIG_INVALID")
        self._buckets_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets"
        self._base_url = f"{self._buckets_url}/{PRIVATE_BUCKET}"
        self._token = api_token
        self._session = session if session is not None else requests.Session()

    @classmethod
    def from_env(cls) -> "PrivateR2Client":
        for name in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
            if not os.environ.get(name):
                raise MoneyForwardStorageError(f"MF_STORAGE_MISSING_{name}")
        return cls(os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_API_TOKEN"])

    def _request(
        self, method: str, suffix: str, *, limit: int,
        body: bytes | None = None, content_type: str | None = None,
        allow_missing: bool = False, bucket_collection: bool = False,
    ) -> bytes | None:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "kiraku-mf-private-storage/1.0",
            "Accept": "application/json" if "/domains/" in suffix else "*/*",
        }
        if content_type:
            headers["Content-Type"] = content_type
        for attempt in range(3):
            response = None
            retry = False
            try:
                # Never follow redirects with credentials or return response text
                # in exceptions: account IDs and financial payloads are private.
                response = self._session.request(
                    method, (self._buckets_url if bucket_collection else self._base_url) + suffix,
                    headers=headers, data=body,
                    timeout=(15, 120), allow_redirects=False, stream=True,
                )
                status = response.status_code
                if status == 404 and allow_missing:
                    return None
                if status == 429 or 500 <= status <= 599:
                    retry = True
                elif not 200 <= status <= 299:
                    raise MoneyForwardStorageError("MF_STORAGE_HTTP_REJECTED")
                else:
                    chunks = []
                    total = 0
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        total += len(chunk)
                        if total > limit:
                            raise MoneyForwardStorageError("MF_STORAGE_RESPONSE_TOO_LARGE")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except requests.RequestException:
                retry = True
            finally:
                if response is not None:
                    response.close()
            if retry and attempt < 2:
                time.sleep(attempt + 1)
        raise MoneyForwardStorageError("MF_STORAGE_TRANSPORT_FAILED")

    def _api_result(self, suffix: str) -> dict[str, Any]:
        raw = self._request("GET", suffix, limit=MAX_API_RESPONSE_BYTES)
        envelope = _decode_json(raw, "MF_STORAGE_PRIVACY_RESPONSE_INVALID")
        if envelope.get("success") is not True or not isinstance(envelope.get("result"), dict):
            raise MoneyForwardStorageError("MF_STORAGE_PRIVACY_RESPONSE_INVALID")
        return envelope["result"]

    def assert_private(self) -> None:
        """Fail closed on public, unknown, missing, or unreadable configuration."""
        managed = self._api_result("/domains/managed")
        if managed.get("enabled") is not False:
            raise MoneyForwardStorageError("MF_STORAGE_MANAGED_DOMAIN_NOT_DISABLED")
        custom = self._api_result("/domains/custom")
        if not isinstance(custom.get("domains"), list):
            raise MoneyForwardStorageError("MF_STORAGE_PRIVACY_RESPONSE_INVALID")
        # Even a disabled registered custom domain is outside this bucket's
        # intended contract; do not silently assume it will remain disabled.
        if custom["domains"]:
            raise MoneyForwardStorageError("MF_STORAGE_CUSTOM_DOMAIN_PRESENT")

    def ensure_private_bucket(self) -> None:
        """Create only after a genuine metadata 404, then verify privacy.

        Authentication/permission/network failures do not authorize creation.
        An existing public or unknown bucket is refused, never reconfigured.
        """
        raw = self._request("GET", "", limit=MAX_API_RESPONSE_BYTES, allow_missing=True)
        if raw is None:
            raw = self._request(
                "POST", "", limit=MAX_API_RESPONSE_BYTES, bucket_collection=True,
                body=json.dumps({"name": PRIVATE_BUCKET}).encode("utf-8"),
                content_type="application/json",
            )
        envelope = _decode_json(raw, "MF_STORAGE_BUCKET_RESPONSE_INVALID")
        result = envelope.get("result")
        if (
            envelope.get("success") is not True
            or not isinstance(result, dict)
            or result.get("name") != PRIVATE_BUCKET
        ):
            raise MoneyForwardStorageError("MF_STORAGE_BUCKET_RESPONSE_INVALID")
        self.assert_private()

    @staticmethod
    def _object_suffix(key: str) -> str:
        if key != SUMMARY_KEY and not STATE_KEY_RE.fullmatch(key):
            raise MoneyForwardStorageError("MF_STORAGE_OBJECT_KEY_INVALID")
        # Cloudflare REST explicitly requires literal slash separators.
        return "/objects/" + quote(key, safe="/")

    def get_object(self, key: str, *, allow_missing: bool = False) -> bytes | None:
        limit = MAX_SUMMARY_BYTES if key == SUMMARY_KEY else MAX_STATE_BYTES
        return self._request(
            "GET", self._object_suffix(key), limit=limit,
            allow_missing=allow_missing,
        )

    def put_object(self, key: str, body: bytes, *, content_type: str) -> None:
        raw = self._request(
            "PUT", self._object_suffix(key), limit=MAX_API_RESPONSE_BYTES,
            body=body, content_type=content_type,
        )
        envelope = _decode_json(raw, "MF_STORAGE_UPLOAD_RESPONSE_INVALID")
        if envelope.get("success") is not True:
            raise MoneyForwardStorageError("MF_STORAGE_UPLOAD_RESPONSE_INVALID")


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise MoneyForwardStorageError("MF_STORAGE_LOCAL_PATH_INVALID")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def _check_sqlite(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            if handle.read(16) != b"SQLite format 3\x00":
                raise MoneyForwardStorageError("MF_STORAGE_STATE_INVALID")
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise MoneyForwardStorageError("MF_STORAGE_STATE_INVALID")
        finally:
            connection.close()
    except sqlite3.Error:
        raise MoneyForwardStorageError("MF_STORAGE_STATE_INVALID") from None


def _write_private(path: Path, body: bytes) -> None:
    with path.open("xb") as handle:
        path.chmod(0o600)
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())


def _storage_pointer(summary: dict[str, Any]) -> tuple[str, str]:
    pointer = summary.get("storage")
    if not isinstance(pointer, dict):
        raise MoneyForwardStorageError("MF_STORAGE_POINTER_INVALID")
    key, digest = pointer.get("state_key"), pointer.get("state_sha256")
    if not isinstance(key, str) or not STATE_KEY_RE.fullmatch(key):
        raise MoneyForwardStorageError("MF_STORAGE_POINTER_INVALID")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise MoneyForwardStorageError("MF_STORAGE_POINTER_INVALID")
    return key, digest


def restore(state_dir: Path, *, client: PrivateR2Client | None = None) -> bool:
    """Restore a committed generation into an empty local state directory.

    Only a 404 on the summary means a first run.  A missing referenced snapshot,
    denied request, invalid manifest, or failed hash is an error, never a reset.
    Existing local state is refused rather than overwritten (including WAL).
    """
    client = client if client is not None else PrivateR2Client.from_env()
    client.assert_private()
    state_dir = Path(state_dir)
    for name in ("ledger.sqlite", "ledger.sqlite-wal", "ledger.sqlite-shm", "summary.json"):
        target = state_dir / name
        if target.exists() or target.is_symlink():
            raise MoneyForwardStorageError("MF_STORAGE_LOCAL_STATE_EXISTS")
    raw_summary = client.get_object(SUMMARY_KEY, allow_missing=True)
    if raw_summary is None:
        _private_directory(state_dir)
        return False
    summary = _decode_json(raw_summary, "MF_STORAGE_SUMMARY_INVALID")
    state_key, expected_hash = _storage_pointer(summary)
    state = client.get_object(state_key)
    if state is None or hashlib.sha256(state).hexdigest() != expected_hash:
        raise MoneyForwardStorageError("MF_STORAGE_STATE_HASH_MISMATCH")
    _private_directory(state_dir)
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=state_dir) as temporary:
        staging = Path(temporary)
        _write_private(staging / "ledger.sqlite", state)
        _write_private(staging / "summary.json", raw_summary)
        _check_sqlite(staging / "ledger.sqlite")
        # Both files are verified before either is exposed.  The workflow never
        # starts sync unless this function succeeds, including the final rename.
        os.replace(staging / "ledger.sqlite", state_dir / "ledger.sqlite")
        os.replace(staging / "summary.json", state_dir / "summary.json")
    return True


def _sqlite_snapshot(source: Path, target: Path) -> bytes:
    if source.is_symlink() or not source.is_file():
        raise MoneyForwardStorageError("MF_STORAGE_STATE_MISSING")
    started_at = time.monotonic()

    def guard_backup(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() - started_at > 30:
            raise MoneyForwardStorageError("MF_STORAGE_BACKUP_TIMEOUT")

    original = copied = None
    try:
        original = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        copied = sqlite3.connect(target)
        target.chmod(0o600)
        # backup() includes committed WAL pages; copying only ledger.sqlite
        # would silently lose them when SQLite is operating in WAL mode.
        original.backup(copied, pages=1024, progress=guard_backup, sleep=0.1)
    except sqlite3.Error:
        raise MoneyForwardStorageError("MF_STORAGE_STATE_INVALID") from None
    finally:
        if copied is not None:
            copied.close()
        if original is not None:
            original.close()
    if target.stat().st_size > MAX_STATE_BYTES:
        raise MoneyForwardStorageError("MF_STORAGE_STATE_TOO_LARGE")
    _check_sqlite(target)
    return target.read_bytes()


def publish(state_dir: Path, *, client: PrivateR2Client | None = None) -> None:
    """Commit a state/summary pair.  Never publish a summary before its state."""
    client = client if client is not None else PrivateR2Client.from_env()
    client.assert_private()
    state_dir = Path(state_dir)
    _private_directory(state_dir)
    summary_path = state_dir / "summary.json"
    if summary_path.is_symlink() or not summary_path.is_file():
        raise MoneyForwardStorageError("MF_STORAGE_SUMMARY_MISSING")
    if summary_path.stat().st_size > MAX_SUMMARY_BYTES:
        raise MoneyForwardStorageError("MF_STORAGE_SUMMARY_TOO_LARGE")
    summary = _decode_json(summary_path.read_bytes(), "MF_STORAGE_SUMMARY_INVALID")
    with tempfile.TemporaryDirectory(prefix=".publish-", dir=state_dir) as temporary:
        staging = Path(temporary)
        snapshot = _sqlite_snapshot(state_dir / "ledger.sqlite", staging / "ledger.sqlite")
        state_key = f"bank-sync/state/{uuid.uuid4().hex}.sqlite"
        summary["storage"] = {
            "state_key": state_key,
            "state_sha256": hashlib.sha256(snapshot).hexdigest(),
        }
        encoded_summary = json.dumps(
            summary, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        if len(encoded_summary) > MAX_SUMMARY_BYTES:
            raise MoneyForwardStorageError("MF_STORAGE_SUMMARY_TOO_LARGE")
        _write_private(staging / "summary.json", encoded_summary)
        client.put_object(state_key, snapshot, content_type="application/octet-stream")
        # This one object is the commit marker consumed by restore and the
        # authenticated UI.  An ambiguous timeout still leaves a valid old/new
        # generation, because the new immutable state was uploaded first.
        client.put_object(SUMMARY_KEY, encoded_summary, content_type="application/json")
        os.replace(staging / "summary.json", summary_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Persist the private MF ledger; no payload logging.")
    parser.add_argument("command", choices=("ensure-bucket", "restore", "publish"))
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    args = parser.parse_args(argv)
    try:
        if args.command == "ensure-bucket":
            PrivateR2Client.from_env().ensure_private_bucket()
            print("MF_STORAGE_BUCKET_PRIVATE")
        elif args.command == "restore":
            restored = restore(args.state_dir)
            print("MF_STORAGE_RESTORED" if restored else "MF_STORAGE_INITIAL_STATE")
        else:
            publish(args.state_dir)
            print("MF_STORAGE_PUBLISHED")
        return 0
    except MoneyForwardStorageError as exc:
        print(exc.code, file=sys.stderr)
    except Exception:
        # Filesystem/SQLite/HTTP exceptions can contain paths, credentials, or
        # payload fragments.  Do not let a traceback reach GitHub Actions logs.
        print("MF_STORAGE_FAILED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
