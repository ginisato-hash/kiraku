"""KIRAKU BI PHASE 2 — deploy trigger hardening.

Before this change, `deploy-worker.yml`/`deploy-staff-worker.yml` triggered
on push to `main` for ANY change under `cloudflare/bi-web/**` /
`cloudflare/staff-ops/**`, including test-only files (e.g. a single
assertion tweak in `test/worker.test.mjs`) — causing an unnecessary
production Worker deploy. This asserts the path filters are scoped to
production-affecting files only, and that a test-only change under
`<worker>/test/**` is provably NOT covered by any of them.
"""
import fnmatch

import yaml

from yuge_finance import config

DEPLOY_WORKER_PATH = config.ROOT / ".github" / "workflows" / "deploy-worker.yml"
DEPLOY_STAFF_WORKER_PATH = config.ROOT / ".github" / "workflows" / "deploy-staff-worker.yml"


def _push_paths(workflow_path):
    wf = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))
    return on["push"]["paths"]


def _matches_any(paths, candidate):
    return any(fnmatch.fnmatch(candidate, pattern) for pattern in paths)


def test_deploy_worker_paths_exclude_test_only_files():
    paths = _push_paths(DEPLOY_WORKER_PATH)
    assert not _matches_any(paths, "cloudflare/bi-web/test/worker.test.mjs")
    assert not _matches_any(paths, "cloudflare/bi-web/README.md")
    assert not _matches_any(paths, "cloudflare/bi-web/.gitignore")


def test_deploy_worker_paths_include_production_files():
    paths = _push_paths(DEPLOY_WORKER_PATH)
    for candidate in [
        "cloudflare/bi-web/src/worker.js",
        "cloudflare/bi-web/src/biRefreshCoordinator.js",
        "cloudflare/bi-web/public/index.html",
        "cloudflare/bi-web/wrangler.toml",
        "cloudflare/bi-web/package.json",
        "cloudflare/bi-web/package-lock.json",
        ".github/workflows/deploy-worker.yml",
    ]:
        assert _matches_any(paths, candidate), f"expected {candidate} to trigger a deploy"


def test_deploy_staff_worker_paths_exclude_test_only_files():
    paths = _push_paths(DEPLOY_STAFF_WORKER_PATH)
    assert not _matches_any(paths, "cloudflare/staff-ops/test/worker.test.mjs")
    assert not _matches_any(paths, "cloudflare/staff-ops/README.md")
    assert not _matches_any(paths, "cloudflare/staff-ops/AUTH_SETUP.md")


def test_deploy_staff_worker_paths_include_production_files():
    paths = _push_paths(DEPLOY_STAFF_WORKER_PATH)
    for candidate in [
        "cloudflare/staff-ops/src/worker.js",
        "cloudflare/staff-ops/public/index.html",
        "cloudflare/staff-ops/wrangler.toml",
        "cloudflare/staff-ops/package.json",
        "cloudflare/staff-ops/package-lock.json",
        ".github/workflows/deploy-staff-worker.yml",
    ]:
        assert _matches_any(paths, candidate), f"expected {candidate} to trigger a deploy"


def test_ci_workflow_is_unaffected_by_path_filter_hardening():
    """CI (pull_request checks) must keep running unconditionally — this
    change only scopes the two deploy workflows' push triggers."""
    ci = yaml.safe_load((config.ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    on = ci.get("on", ci.get(True))
    assert "pull_request" in on
    assert "paths" not in (on.get("pull_request") or {})
