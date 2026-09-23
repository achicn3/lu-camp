#!/bin/bash
# IP 換了、或改了 frontend/.env.local 之後要跑這支：NEXT_PUBLIC_* 是 build 當下
# 寫死進 JS 的，不重新 build 不會生效。跑完會自動重啟 launchd 管的 frontend 服務。
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_DIR/frontend"
/opt/homebrew/bin/pnpm run build
launchctl kickstart -k "gui/$(id -u)/com.lucamp.frontend"
echo "frontend 已重新 build 並重啟"
