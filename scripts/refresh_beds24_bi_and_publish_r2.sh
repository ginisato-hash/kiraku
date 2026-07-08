#!/bin/bash
set -euo pipefail

PROJECT_DIR="/Users/ginisato/YugeFinance/kiraku-finance-automation"

# launchdはPATHが最小限(/usr/bin:/bin:/usr/sbin:/sbin)のため、
# publish-bi-r2が呼ぶ npx/wrangler(/usr/local/bin または /opt/homebrew/bin 配下)を明示的に通す。
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

cd "$PROJECT_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] start"

./.venv/bin/yuge-finance refresh-beds24-bi --auto-months-with-bookings --publish

./.venv/bin/yuge-finance publish-bi-r2

curl -s https://kiraku-bi.s-sato-dce.workers.dev/api/manifest | python3 -m json.tool | grep generated_at_jst || true

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] done"
