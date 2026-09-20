#!/bin/bash
# run_bi_refresh_once.sh — "one BI refresh job" as a single, portable unit.
#
# This is the Cloud Run migration boundary requested alongside the
# event-driven BI refresh work: today this script is NOT wired into
# .github/workflows/refresh-bi-r2.yml (that workflow keeps its existing
# inline steps unchanged, to avoid touching the 20+ existing regression
# tests in tests/test_github_actions_bi_refresh.py that assert on the
# workflow YAML's literal text). Instead, this script mirrors those same
# steps as a standalone artifact, proven equivalent by
# tests/test_run_bi_refresh_once_script.py, so a future Cloud Run Job can
# call it directly without needing GitHub Actions at all — only the
# platform-specific setup (checkout, Python/Node install, `npx wrangler`
# availability, credentials) differs between the two call sites; the
# business logic below is identical either way.
#
# NOT the same as scripts/refresh_beds24_bi_and_publish_r2.sh (that one is
# the pre-existing macOS launchd fallback — see README.md section 12 —
# hardcoded to a local .venv path and Mac-specific PATH entries, kept only
# as a manual/emergency fallback). This script assumes `yuge-finance` and
# `npx`/`wrangler` are already on PATH (true both in refresh-bi-r2.yml
# after `pip install -e .`, and in any future container image built for
# this job) and never hardcodes a local machine path.
#
# Required environment variables (see .github/workflows/refresh-bi-r2.yml
# for the GitHub Actions Secrets that supply these today):
#   BEDS24_LONG_LIFE_TOKEN   Beds24 v2 Long Life Token
#   BEDS24_PROPERTY_IDS      喜らく単体のBeds24 property id ("330695")
#   CLOUDFLARE_ACCOUNT_ID
#   CLOUDFLARE_API_TOKEN
#   CLOUDFLARE_R2_BUCKET
# Optional:
#   STAFF_OPS_AUTH_CONFIRMED  "1" を設定するとStaff Ops (Daily Ops/清掃
#                             指示書)データも生成・R2 publishする。未設定/
#                             それ以外の値なら、既存の運用者ゲートと同じく
#                             このステップ群を丸ごとスキップする。
set -euo pipefail

export TZ="${TZ:-Asia/Tokyo}"

echo "[run_bi_refresh_once] start $(date -u +%Y-%m-%dT%H:%M:%SZ)"

yuge-finance refresh-beds24-bi --auto-months-with-bookings --publish

yuge-finance publish-bi-r2 --bucket "$CLOUDFLARE_R2_BUCKET" --preserve-bank-fields-from-r2

if [ "${STAFF_OPS_AUTH_CONFIRMED:-}" = "1" ]; then
  yuge-finance export-daily-ops

  if npx wrangler r2 bucket create kiraku-staff-ops-data 2>&1 | tee /tmp/run_bi_refresh_once_r2_create.log; then
    echo "[run_bi_refresh_once] staff-ops R2 bucket ensured (created or already existed)."
  elif grep -qi "already exists" /tmp/run_bi_refresh_once_r2_create.log; then
    echo "[run_bi_refresh_once] staff-ops R2 bucket already existed."
  else
    echo "[run_bi_refresh_once] ERROR: failed to ensure R2 bucket kiraku-staff-ops-data." >&2
    exit 1
  fi

  uploaded=0
  for attempt in 1 2 3; do
    if npx wrangler r2 object put kiraku-staff-ops-data/latest/staff_ops_snapshot.json \
      --file data/output/latest/ops/staff_ops_snapshot.json \
      --content-type application/json \
      --remote; then
      uploaded=1
      break
    fi
    echo "[run_bi_refresh_once] WARNING: publish Daily Ops data to R2 attempt $attempt failed"
    [ "$attempt" -lt 3 ] && sleep 5
  done
  if [ "$uploaded" -ne 1 ]; then
    echo "[run_bi_refresh_once] ERROR: failed to publish Daily Ops data to R2 after 3 attempts." >&2
    exit 1
  fi
else
  echo "[run_bi_refresh_once] STAFF_OPS_AUTH_CONFIRMED != '1' — skipping Daily Ops/cleaning data export+publish."
fi

echo "[run_bi_refresh_once] done $(date -u +%Y-%m-%dT%H:%M:%SZ)"
