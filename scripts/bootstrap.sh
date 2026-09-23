#!/bin/bash
# 新機器一鍵裝好 lu-camp：Homebrew 套件 → 依賴 → Postgres 角色/資料庫 → migration →
# frontend 正式版建置 → 四個 launchd 服務（postgres/backend/frontend/hardware-agent）。
#
# 冪等：可重複執行，已裝好的東西會跳過。**不會**幫你建店（店名/統編/三個帳號密碼），
# 那一步刻意留給人工執行 setup_new_store.py（見腳本自己的 docstring）——密碼與店家
# 識別本來就不該有預設值，寫進 bootstrap 腳本等於變相給了預設值。
#
# 執行：./scripts/bootstrap.sh
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BREW="/opt/homebrew/bin/brew"
UV="/opt/homebrew/bin/uv"
PNPM="/opt/homebrew/bin/pnpm"
PG_BIN="/opt/homebrew/opt/postgresql@16/bin"

echo "==> lu-camp bootstrap：$REPO_DIR"

if ! command -v "$BREW" >/dev/null 2>&1; then
    echo "找不到 Homebrew（$BREW）。請先手動安裝：https://brew.sh" >&2
    exit 1
fi

echo "==> [1/9] Homebrew 套件（uv / pnpm / node / postgresql@16）"
for formula in uv pnpm node postgresql@16; do
    "$BREW" list "$formula" >/dev/null 2>&1 || "$BREW" install "$formula"
done

echo "==> [2/9] backend 依賴（釘 Python 3.12，符合 CLAUDE.md §3 下限）"
cd "$REPO_DIR/backend"
"$UV" python pin 3.12 >/dev/null
"$UV" sync

echo "==> [3/9] hardware-agent 依賴"
cd "$REPO_DIR/hardware-agent"
"$UV" sync

echo "==> [4/9] frontend 依賴"
cd "$REPO_DIR/frontend"
"$PNPM" install

echo "==> [5/9] 啟動 PostgreSQL（brew services，開機自啟）"
"$BREW" services start postgresql@16 >/dev/null
for _ in $(seq 1 20); do
    "$PG_BIN/pg_isready" -h 127.0.0.1 -p 5432 >/dev/null 2>&1 && break
    sleep 1
done
"$PG_BIN/pg_isready" -h 127.0.0.1 -p 5432

echo "==> [6/9] 根目錄 .env（金鑰/DB 密碼；沒有就在這台機器現場生成，不沿用任何其他機器的值）"
ENV_FILE="$REPO_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    PG_PASS=$(openssl rand -hex 16)
    cat > "$ENV_FILE" <<EOF
# $(date +%Y-%m-%d) 由 scripts/bootstrap.sh 在這台機器上現場生成，未從任何機器複製。
POSTGRES_USER=lucamp
POSTGRES_PASSWORD=${PG_PASS}
POSTGRES_DB=lucamp

DATABASE_URL=postgresql+asyncpg://lucamp:${PG_PASS}@127.0.0.1:5432/lucamp
APP_ENV=development

SECRET_KEY=$(openssl rand -hex 32)
PII_ENC_KEY=$(openssl rand -base64 32)
HMAC_KEY=$(openssl rand -hex 32)
EOF
    echo "    寫了新的 $ENV_FILE"
else
    echo "    已存在，不覆寫：$ENV_FILE"
fi
POSTGRES_PASSWORD="$(grep '^POSTGRES_PASSWORD=' "$ENV_FILE" | cut -d= -f2)"

echo "==> [7/9] lucamp DB 角色/資料庫（冪等）"
# SUPERUSER：這是這台機器專用、不對外開放的本機開發庫，非共用/正式環境。給
# superuser 是刻意的——backend 測試套件有幾支真 commit 的並發測試要用
# SET session_replication_role 繞開 trigger 順序問題，那個操作只有 superuser 能做，
# 少了它整包 pytest 會在毫無關聯的測試上炸出一長串 FK 違反（2026-09-02 實測踩過）。
if ! "$PG_BIN/psql" -h 127.0.0.1 -p 5432 -U "$(whoami)" -d postgres -tAc \
    "SELECT 1 FROM pg_roles WHERE rolname='lucamp'" | grep -q 1; then
    "$PG_BIN/psql" -h 127.0.0.1 -p 5432 -U "$(whoami)" -d postgres -v ON_ERROR_STOP=1 \
        -c "CREATE ROLE lucamp LOGIN SUPERUSER PASSWORD '${POSTGRES_PASSWORD}'"
else
    "$PG_BIN/psql" -h 127.0.0.1 -p 5432 -U "$(whoami)" -d postgres -v ON_ERROR_STOP=1 \
        -c "ALTER ROLE lucamp WITH LOGIN SUPERUSER PASSWORD '${POSTGRES_PASSWORD}'"
fi
if ! "$PG_BIN/psql" -h 127.0.0.1 -p 5432 -U "$(whoami)" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='lucamp'" | grep -q 1; then
    "$PG_BIN/psql" -h 127.0.0.1 -p 5432 -U "$(whoami)" -d postgres -c "CREATE DATABASE lucamp OWNER lucamp"
fi

echo "==> [8/9] Alembic migration"
cd "$REPO_DIR/backend"
set -a; source "$ENV_FILE"; set +a
"$UV" run alembic upgrade head

echo "==> [8.5/9] hardware-agent/.env（沒有就先建成 fake 模式——這台目前是否接真機由人工確認再切換）"
HW_ENV="$REPO_DIR/hardware-agent/.env"
if [ ! -f "$HW_ENV" ]; then
    cat > "$HW_ENV" <<EOF
# 由 scripts/bootstrap.sh 產生。預設 fake（不列印）——接上真機後改 AGENT_DEVICES=real，
# 並依 .env.example 的說明填各台印表機固定 IP，改完
# launchctl kickstart -k gui/\$(id -u)/com.lucamp.hardware-agent 生效。
AGENT_DEVICES=fake
AGENT_BACKEND_URL=http://127.0.0.1:8000
AGENT_CORS_ORIGINS=http://localhost:3000
EOF
    echo "    寫了新的 $HW_ENV（fake 模式，記得之後視情況把 AGENT_CORS_ORIGINS 加上這台的內網 IP）"
else
    echo "    已存在，不覆寫：$HW_ENV"
fi

echo "==> [8.7/9] frontend/.env.local（沒有就抓目前內網 IP 寫入——IP 還沒固定的話，之後換 IP 記得跑 rebuild-frontend.sh）"
FE_ENV="$REPO_DIR/frontend/.env.local"
if [ ! -f "$FE_ENV" ]; then
    LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "127.0.0.1")"
    cat > "$FE_ENV" <<EOF
NEXT_PUBLIC_API_BASE_URL=http://${LAN_IP}:8000
NEXT_PUBLIC_AGENT_URL=http://localhost:8001
EOF
    echo "    寫了新的 $FE_ENV（偵測到內網 IP：$LAN_IP）"
else
    echo "    已存在，不覆寫：$FE_ENV"
fi

echo "==> [9/9] frontend 正式版建置 + 安裝四個 launchd 服務"
cd "$REPO_DIR/frontend"
"$PNPM" run build
"$REPO_DIR/scripts/launchd/install-launchd.sh"

echo
echo "==> Bootstrap 完成。"
echo "    這台若還是空庫（第一次開店），還沒有任何帳號——手動跑："
echo "      cd backend && STORE_NAME=... STORE_TAX_ID=... MANAGER_PASSWORD=... \\"
echo "        CLERK_PASSWORD=... KIOSK_PASSWORD=... uv run python -m app.scripts.setup_new_store"
echo "    （見該腳本 docstring；密碼與店家識別故意不給預設值。）"
