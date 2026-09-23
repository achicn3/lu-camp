#!/bin/bash
# launchd 用啟動腳本：hardware-agent。
# 入口是 build_app（非舊的 app），它會自動讀 hardware-agent/.env（若存在），
# AGENT_DEVICES 沒設或打錯字會直接拒絕啟動（刻意的 fail-loud，見 agent/main.py）。
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_DIR/hardware-agent"
exec /opt/homebrew/bin/uv run uvicorn agent.main:build_app --factory --host 0.0.0.0 --port 8001
