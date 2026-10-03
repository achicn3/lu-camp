#!/bin/bash
# IP 換了、或改了 frontend/.env.local 之後要跑這支：NEXT_PUBLIC_* 是 build 當下
# 寫死進 JS 的，不重新 build 不會生效。跑完會自動重啟 launchd 管的 frontend 服務。
#
# 位址一律以 frontend/.env.local 為準：Next 建置時「shell 環境變數」優先於 .env.local，
# 升級步驟前面 `source ../.env` 過的話，那份檔案裡的舊位址會被寫進 JS、整台店連不到後端
# （2026-10-04 實際發生）。所以這裡先把 .env.local 的值明確 export 蓋掉，建完再驗一次。
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_DIR/frontend"

ENV_LOCAL=".env.local"
read_local() { grep -E "^$1=" "$ENV_LOCAL" | tail -1 | cut -d= -f2- | tr -d '"' | tr -d "'"; }
API_URL="$(read_local NEXT_PUBLIC_API_BASE_URL || true)"
AGENT_URL="$(read_local NEXT_PUBLIC_AGENT_URL || true)"
if [[ -z "$API_URL" || -z "$AGENT_URL" ]]; then
  echo "中止：$REPO_DIR/frontend/$ENV_LOCAL 沒有 NEXT_PUBLIC_API_BASE_URL／NEXT_PUBLIC_AGENT_URL" >&2
  exit 1
fi
export NEXT_PUBLIC_API_BASE_URL="$API_URL"
export NEXT_PUBLIC_AGENT_URL="$AGENT_URL"

/opt/homebrew/bin/pnpm run build

# 建出來的 JS 裡必須是 .env.local 的位址；不是就不要重啟（舊的前端還能用，換上去整店斷線）。
if ! grep -rqF "$API_URL" .next/static/chunks || ! grep -rqF "$AGENT_URL" .next/static/chunks; then
  echo "中止：建出來的前端沒有寫入 $API_URL／$AGENT_URL（位址被別的設定蓋掉），未重啟 frontend" >&2
  exit 1
fi

launchctl kickstart -k "gui/$(id -u)/com.lucamp.frontend"
echo "frontend 已重新 build 並重啟（後端 $API_URL、硬體代理 $AGENT_URL）"
