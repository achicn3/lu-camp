# launchd 開機自啟（Phase 2）

常駐服務用 `RunAtLoad` + `KeepAlive`（當機自動重啟，`ThrottleInterval=10`）：

| 服務 | plist | 管理方式 |
|---|---|---|
| PostgreSQL | `homebrew.mxcl.postgresql@16` | `brew services`（非本目錄，Homebrew 自己產生） |
| backend | `com.lucamp.backend.plist` | 本目錄 |
| frontend | `com.lucamp.frontend.plist` | 本目錄（`next start`，正式版建置） |
| hardware-agent | `com.lucamp.hardware-agent.plist` | 本目錄 |

另有一個**排程工作**（不是常駐服務，`KeepAlive=false`，跑完就結束）：

| 工作 | plist | 說明 |
|---|---|---|
| 每日資料庫備份 | `com.lucamp.daily-backup.plist` | 每天 04:00 跑 `run-daily-backup.sh` |

這裡的 `.plist.template` 是**存底**（版控用），用 `__REPO_DIR__` / `__HOME__` 佔位、不寫死
任何一台機器的路徑；實際生效的副本（真實路徑）由 `install-launchd.sh` 產生到
`~/Library/LaunchAgents/`。

## 安裝（新機器最省事：直接跑 `scripts/bootstrap.sh`）

新機器從零開始，用 repo 根目錄的 `scripts/bootstrap.sh`：裝 Homebrew 套件、
backend/frontend/hardware-agent 依賴、Postgres 角色與 migration、frontend 正式版建置，
最後才呼叫這裡的 `install-launchd.sh` 把四個服務裝起來、開機自啟。冪等，可重跑。

只想重裝/重啟 launchd 這一層（例如改了 plist template、或 rebuild 過 frontend 之後想
確保服務吃到新版），單獨跑這支就好：

```bash
scripts/launchd/install-launchd.sh
```

它會把每個 template 套用目前機器的路徑、(re)bootstrap 三個服務＋每日備份排程；postgres 另外
`brew services start postgresql@16`（`bootstrap.sh` 裡已含這一步）。

## 常用操作

```bash
# 重啟單一服務（改了 .env 之後）
launchctl kickstart -k gui/$(id -u)/com.lucamp.backend

# 換了 IP、frontend/.env.local 之後（NEXT_PUBLIC_* 是 build 當下寫死進 JS）
scripts/launchd/rebuild-frontend.sh

# 看狀態 / 看 log
launchctl print gui/$(id -u)/com.lucamp.backend | grep state
tail -f ~/Library/Logs/lucamp/backend.log

# 停用（不刪 plist，下次開機不會再自動載入直到重新 bootstrap）
launchctl bootout gui/$(id -u)/com.lucamp.backend
```

## 每日備份（`run-daily-backup.sh`）

本機 `pg_dump` → 驗 `pg_restore --list` 可讀 → 就位 → 只保留最新 7 份。備份位置取
`.env` 的 `BACKUP_LOCAL_DIR`，與 app 內建備份模組同一個目錄。

刻意的設計，改動前請先理解：

- **先寫 `.partial`、驗過才 `mv` 就位**：中途失敗不會留下半截檔冒充當日備份。
- **修剪一定排在備份成功之後**：否則備份壞掉那天會把僅存的好備份一起刪光。
- 只刪 `<db>_YYYYMMDD.dump` 這個每日樣式，手動／升級前的備份（`*_preupgrade_*` 等）不動。
- 密碼從 `.env` 讀、走 `PGPASSWORD` 環境變數，不進指令列（`ps` 看不到）。
- 目錄 700、檔案 600（dump 含姓名電話等個資）。
- macOS 內建 bash 是 3.2，**沒有 `mapfile`**；這支刻意只用 3.2 有的語法。

> ⚠️ 這是**同一台機器上的本機備份**，防得了誤刪與升級失敗，**防不了整機滅失／失竊／火災**。
> 異地加密備份走 app 的備份模組上傳 R2（見 `docs/28`），兩者互為保險，都要留著。

## 已知事項

- frontend 走 `next start`（正式版），不是 `next dev`。`NEXT_PUBLIC_API_BASE_URL` 與
  `NEXT_PUBLIC_AGENT_URL` 都指到這台 Mac 的固定內網 IP（路由器已做 DHCP 綁定）。
  IP 換了要跑 `scripts/launchd/rebuild-frontend.sh` 重新 build 才會生效。
- backend 的 `.env`（金鑰/DB 密碼）與 hardware-agent 的 `.env`（含 CORS 來源、真機 IP）都
  不入 repo；程式啟動時各自從自己的目錄讀，plist 不需要另外帶環境變數。
