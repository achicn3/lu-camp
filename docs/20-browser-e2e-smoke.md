# 20 — 瀏覽器 E2E 煙霧測試（Playwright，無 root WSL 配方）

本檔記錄在本機（無 sudo 的 WSL）跑前端 Playwright 煙霧測試的完整步驟，讓每個 Phase 的
前端都能對「真 backend + 真 Postgres」做一次端對端驗證並截圖，**不必每次重裝環境**。

煙霧腳本位於 `frontend/scripts/*-smoke.mjs`（如 `consignment-smoke.mjs`、`acquisition-smoke.mjs`）。

## 0. 一次性環境安裝（冪等，可重複跑）

```bash
source frontend/scripts/setup-browser-e2e.sh
```

做兩件事，已安裝就跳過：

1. 下載 headless chromium 缺的系統庫（libnspr4/libnss3/…）。無 sudo → 用 `apt-get download`
   取 `.deb`、`dpkg -x` 解到 `~/.cache/lu-camp-e2e/pwlibs`（持久），再用 `LD_LIBRARY_PATH` 指向。
2. 裝 WenQuanYi 正黑 CJK 字型到 `~/.local/share/fonts`，否則截圖中文會變方框。

跑完它會 `export LU_CAMP_PW_LDPATH`，後續執行 node 前帶上：

```bash
export LD_LIBRARY_PATH="$LU_CAMP_PW_LDPATH:$LD_LIBRARY_PATH"
```

> Playwright 瀏覽器本體（chrome-headless-shell）由 `pnpm` 安裝時已下載到 `~/.cache/ms-playwright`，
> 此處只補它缺的系統庫與字型。

## 1. Postgres（已起則跳過）

Docker Desktop 非 WSL 整合，用 Windows 端執行檔；容器 `lu-camp-db-1` 對應 `127.0.0.1:1234`
（帳密 `lucamp` / `lucamp_dev_pw`）。建一個 E2E 專用庫：

```bash
DOCKER="/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe"
"$DOCKER" exec lu-camp-db-1 psql -U lucamp -d postgres \
  -c "DROP DATABASE IF EXISTS lucamp_e2e" -c "CREATE DATABASE lucamp_e2e"
```

## 2. 後端：migration + seed + 啟動（:8000）

```bash
cd backend
export DATABASE_URL=postgresql+asyncpg://lucamp:lucamp_dev_pw@127.0.0.1:1234/lucamp_e2e
export APP_ENV=development
export SECRET_KEY="dev0secret0key0do0not0use0in0prod0000000000000000000000000000"
export PII_ENC_KEY="hKq2EfqmY84r6zuGQj4/fqFjn4DWIpzSkv+b5wYzh/k="
export HMAC_KEY="eeacaeb328e7afd580365221418c386e1fc80b0b5e2d7025e38fd430cc8edf2b"
export CORS_ORIGINS="http://localhost:3000"

uv run alembic upgrade head
uv run python -m app.scripts.seed_dev_store
ALLOW_DEV_SEED=true SEED_USER_PASSWORD=dev-test-123456 uv run python -m app.scripts.seed_dev_user
ALLOW_DEV_SEED=true uv run python -m app.scripts.seed_dev_consignment   # 寄售頁專用測資

uv run uvicorn app.main:app --host 0.0.0.0 --port 8000   # 背景執行
```

> 各 Phase 自備對應 `seed_dev_*`（見 `app/scripts/`）。登入帳密：`dev-manager` / `dev-test-123456`。

## 3. 前端（:3000）

前端 client 端 API base 預設 `http://localhost:8000`（`NEXT_PUBLIC_API_BASE_URL` 可覆寫）。

```bash
cd frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm dev   # 背景執行；若 3000 已被佔用先停掉舊的
```

## 4. 跑煙霧腳本 + 截圖

```bash
export LD_LIBRARY_PATH="$LU_CAMP_PW_LDPATH:$LD_LIBRARY_PATH"
SMOKE_BASE=http://localhost:3000 node frontend/scripts/consignment-smoke.mjs
```

截圖預設輸出到 `~/tmp/lu-camp-shots/<feature>/`（可用 `SMOKE_SHOTS` 覆寫）。腳本最後印
`N/N 通過`，全過 exit 0。

## 5. 收尾

```bash
pkill -f "uvicorn app.main:app"        # 停後端
"$DOCKER" exec lu-camp-db-1 psql -U lucamp -d postgres -c "DROP DATABASE IF EXISTS lucamp_e2e"
```

## 備註

- 截圖中文若仍是方框 → 第 0 步字型沒裝成功，重跑 `setup-browser-e2e.sh` 並確認
  `fc-list | grep -i zenhei` 有輸出。
- 想完全容器化（避免依賴本機 apt）可改用官方 `mcr.microsoft.com/playwright` 映像跑 node 腳本；
  本配方走「家目錄解 .deb」是為了在現有無 root WSL 直接可用。
