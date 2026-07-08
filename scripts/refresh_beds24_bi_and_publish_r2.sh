#!/bin/bash
set -euo pipefail

PROJECT_DIR="/Users/ginisato/YugeFinance/kiraku-finance-automation"

# launchdはPATHが最小限(/usr/bin:/bin:/usr/sbin:/sbin)のため、
# publish-bi-r2が呼ぶ npx/wrangler(/usr/local/bin配下)を明示的に通す。
export PATH="/usr/local/bin:$PATH"

cd "$PROJECT_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] refresh_beds24_bi_and_publish_r2 start"

./.venv/bin/yuge-finance refresh-beds24-bi --auto-months-with-bookings --publish

./.venv/bin/yuge-finance publish-bi-r2

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] refresh_beds24_bi_and_publish_r2 done"
