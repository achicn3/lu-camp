#!/bin/bash
# launchd 用啟動腳本：backend（FastAPI）。
# .env 由 app/core/config.py 的 pydantic-settings 自動讀根目錄 .env，這裡不必自己 source。
# .env.r2（R2 備份憑證，docs/31）則不在 pydantic-settings 的 env_file 內，須在啟動前
# source 成 OS 環境變數（OS 環境變數優先於 .env 檔）；沒有這個檔案時（例如尚未設定備份）
# 略過，backend 仍可正常啟動、只是備份功能停用。
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
if [ -f "$REPO_DIR/.env.r2" ]; then
    set -a
    source "$REPO_DIR/.env.r2"
    set +a
fi
cd "$REPO_DIR/backend"
exec /opt/homebrew/bin/uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
