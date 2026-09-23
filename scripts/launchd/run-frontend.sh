#!/bin/bash
# launchd 用啟動腳本：frontend（Next.js 正式版）。
# 用 next start，不用 next dev——unattended 常駐服務要吃正式版的穩定性/效能，
# dev 模式的 HMR/allowedDevOrigins 是給人在旁邊改 code 用的，不是給服務跑一整天用的。
# NEXT_PUBLIC_* 在 `pnpm run build` 當下已經寫死進 JS；換 IP 要重新 build
# （見 scripts/launchd/rebuild-frontend.sh），這支只負責啟動已經 build 好的產物。
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_DIR/frontend"
exec /opt/homebrew/bin/pnpm exec next start -H 0.0.0.0 -p 3000
