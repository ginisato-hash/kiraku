#!/bin/bash
set -euo pipefail

PROJECT_DIR="/Users/ginisato/YugeFinance/kiraku-finance-automation"

# launchdはPATHが最小限(/usr/bin:/bin:/usr/sbin:/sbin)のため、
# publish-bi-r2が呼ぶ npx/wrangler(/usr/local/bin または /opt/homebrew/bin 配下)を明示的に通す。
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

cd "$PROJECT_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] start"

./.venv/bin/yuge-finance refresh-beds24-bi --auto-months-with-bookings --publish

./.venv/bin/yuge-finance publish-bi-r2 --preserve-bank-fields-from-r2

# 公開後のmanifest確認。BIのWorker公開URLは管理用ゲートの内側にあるため、公開GETではなく
# R2のmanifestを wrangler で認証付きに直接読む（失敗しても止めない補助確認）。
manifest_tmp="$(mktemp)"
(cd cloudflare/bi-web && npx wrangler r2 object get kiraku-bi-data/latest/manifest.json --file "$manifest_tmp" --remote >/dev/null 2>&1) \
  && python3 -m json.tool "$manifest_tmp" | grep generated_at_jst || true
rm -f "$manifest_tmp"

echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] done"
