# 43 — MacBook 正式機：升級到 2026-09-26 版（排隊收購、待整理上架、顧客螢幕動畫）

> **給在 MacBook（店內正式機）上工作的 AI agent：** 這份文件是一項**待你執行的升級任務**。
> 請照「§2 動手前」→「§3 升級步驟」→「§4 驗證」依序做完，每一步的**實際輸出**都要回報給店主。
> 任何一步失敗或出現文件沒寫到的狀況：**停下來、把錯誤原文給店主看、不要自己猜著修**。
> 一定要在**打烊後**做（升級中會停後端約數分鐘）。

## 1. 這次多了什麼、為什麼要下指令

| 變更 | 影響到 | 要做的事 |
|---|---|---|
| 排隊收購：付款成立收購、商品建成「待整理」（docs/42 I3） | 資料庫 | migration `a1d6e8f3b5c9`（商品狀態加「待整理」、批次付款欄位、批次↔收購對應表） |
| 上架時記差異（少了／壞了） | 資料庫 | migration `b7e2c4d9f1a3`（差異紀錄表） |
| 待整理上架頁、收購紀錄進度、庫存價值「待整理」列 | 後端＋前端 | 重新 build 前端、重啟後端 |
| 顧客螢幕待機畫面店名動畫（React Bits SplitText） | 前端**新套件** `gsap`、`@gsap/react` | **一定要 `pnpm install`**，否則前端 build 會失敗 |
| 收購明細（含簽名）可列出整批多張收購單號 | 硬體代理 | `uv sync`＋重啟代理（舊代理也能印，只是只印第一張單號） |
| 設定頁新增「收購付錢前一定要客人簽名」開關 | 設定 | 升級後**預設關閉**，要不要打開由店主決定（§5） |
| 收購頁「買斷」可以再加散裝，一起簽名付款（收購①） | 後端＋前端 | 同上：重啟後端、重新 build 前端（沒有新的 migration） |

> 如果正式機的版本比 2026-09-23 更舊，中間還有別的 migration；`alembic upgrade head` 會一次補齊，不用逐一處理。

## 2. 動手前（不要跳過）

1. **記下現在的版本**（出問題要退回這裡）：
   ```bash
   cd <repo 目錄> && git log -1 --oneline
   ```
2. **確認已打烊、沒有人在結帳或收購**（問店主）。
3. **先做一次備份**，並確認備份檔真的產生：
   ```bash
   scripts/launchd/run-daily-backup.sh
   ls -lt "$(grep ^BACKUP_LOCAL_DIR= .env | cut -d= -f2)" | head -3
   ```
   最新一份的時間要是「剛剛」。沒有新檔就**停下來**，不要往下做。
4. **確認工作目錄乾淨**：`git status --short` 應該沒有輸出。有的話把輸出給店主看，不要自己丟棄。

## 3. 升級步驟

```bash
cd <repo 目錄>

# 3.1 停後端（避免升級資料庫時還有人在寫）
launchctl bootout gui/$(id -u)/com.lucamp.backend

# 3.2 拉最新程式
git pull --ff-only origin main
git log -1 --oneline            # 回報這行

# 3.3 後端依賴＋資料庫升級
cd backend
/opt/homebrew/bin/uv sync
set -a; source ../.env; set +a
/opt/homebrew/bin/uv run alembic upgrade head
/opt/homebrew/bin/uv run alembic current   # 應顯示 b7e2c4d9f1a3 (head)
cd ..

# 3.4 硬體代理依賴
cd hardware-agent && /opt/homebrew/bin/uv sync && cd ..

# 3.5 前端：先裝新套件，再 build（build 完會自動重啟前端服務）
cd frontend && /opt/homebrew/bin/pnpm install && cd ..
scripts/launchd/rebuild-frontend.sh

# 3.6 把服務裝回去並重啟（後端在 3.1 被停掉了，這一步會重新載入；也會重啟代理）
scripts/launchd/install-launchd.sh
```

- `pnpm install` 若問要不要重建 `node_modules`（非互動模式會直接中止），改用 `CI=true /opt/homebrew/bin/pnpm install`。
- `alembic upgrade head` 失敗：**不要重跑、不要降版**，把錯誤原文給店主，然後照 §6 退回。

## 4. 驗證（每一項都回報結果）

```bash
curl -s -o /dev/null -w "backend %{http_code}\n" http://127.0.0.1:8000/api/v1/health
curl -s -o /dev/null -w "frontend %{http_code}\n" http://127.0.0.1:3000/login
launchctl print gui/$(id -u)/com.lucamp.backend | grep state
launchctl print gui/$(id -u)/com.lucamp.frontend | grep state
launchctl print gui/$(id -u)/com.lucamp.hardware-agent | grep state
```

三個 `state` 都要是 `running`、兩個 http 都是 `200`。接著請**店主**在瀏覽器確認：

1. 顧客螢幕（平板）回到待機時，店名一個字一個字浮上來，最後完整顯示。
2. 「排隊收購」頁右上角有「待整理上架」按鈕，點進去打得開（沒有待整理商品時顯示「目前沒有待整理的商品」）。
3. 「收購」頁照常能收一筆（可以用測試賣方收一件再作廢）。
4. 報表 → 帳務 → 庫存價值，表格多一列「待整理（已付款、還沒上架）」。
5. 設定頁有「收購付錢前一定要客人在顧客螢幕簽名」開關。

## 5. 升級後給店主決定的事

- **「收購付錢前一定要客人簽名」**：預設關閉。打開後，收購頁與排隊收購都必須先讓客人在顧客螢幕簽名才能付款。
  請店主決定要不要打開（設定 → 一般設定 → 勾選 → 儲存一般設定）。**不要替店主打開。**

## 6. 出問題怎麼退

- **只退程式、不要降資料庫**：`alembic downgrade` 會刪掉差異紀錄與批次對應資料，**不可以做**。
- 只退程式的前提：升級後**還沒有任何排隊收購付過款**。一旦有商品變成「待整理」，舊程式讀到這個
  新狀態會出錯（庫存頁、報表打不開）——這時**不要退**，把狀況回報店主，由開發端修正。
  ```bash
  git checkout <§2.1 記下的 commit>
  cd frontend && /opt/homebrew/bin/pnpm install && cd ..
  scripts/launchd/rebuild-frontend.sh
  scripts/launchd/install-launchd.sh
  ```
- 資料庫壞了才用 §2.3 的備份還原，步驟見 `docs/28-backup-restore-runbook.md`，**還原前一定先問店主**。
