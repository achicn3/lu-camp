# 87 — 線上點餐正式版：雲端資源、接上 MacBook、上線前檢查清單

> 2026-10-15 建立。正式網址 **https://order.lukeng-camp.com**（網域 `lukeng-camp.com` 由店主在 Cloudflare 購買）。
> 測試版（staging）照舊在 `lu-camp-online-order-staging.noiping2.workers.dev`，兩者資料庫、照片、密鑰完全分開（docs/44 §8.5）。

## 1. 雲端資源（已建好）

| 項目 | 名稱 | 備註 |
|---|---|---|
| Worker | `lu-camp-online-order`（`wrangler deploy --env production`） | 綁自訂網域 `order.lukeng-camp.com`；不開 workers.dev 網址 |
| D1 | `lu-camp-online-order-production`（APAC，id `66b0382d-…`） | migrations 0001–0012 已套用 |
| R2 | `lu-camp-online-order-production`（APAC） | 菜單照片、字型 |
| 變數 | `STORE_ID=1`、`TURNSTILE_SITE_KEY`（公開值，寫在 `wrangler.jsonc`） | |
| 密鑰 | `INTEGRATION_SECRET`、`TURNSTILE_SECRET`（`wrangler secret put --env production`） | **不進 repo**；開發機副本在 repo 外的 `.env.online-order-production`（600） |
| LINE Pay | 未設定 → 客人頁只有現金 | 店主確認正式收款資格後再設 `LINEPAY_*` 三項 |

正式資料庫**沒有任何示範資料**：開發機的示範環境不接正式雲端。MacBook 接上之前，雲端收不到 POS 心跳，
客人頁自動顯示暫停接單、沒有菜單（`/api/status` 回 `accepting: false`、`/api/menu` 回 `menu_not_published`）。

## 2. 網域與防護設定（已設定）

- Always Use HTTPS：開（http 一律轉 https）；最低 TLS 1.2；Automatic HTTPS Rewrites：開。憑證由 Cloudflare 自動簽發。
- **速率限制規則**（免費方案 1 條）：`order.lukeng-camp.com` 的 `/api/*`，同一 IP（同機房）每 10 秒超過 **120** 次就擋 10 秒。
  門檻放寬是因為店內 Wi-Fi 的客人共用一個對外 IP，訂單頁每 5 秒更新一次。`/integration/*`（POS 拉單，HMAC 驗證）不在此限。
  2026-10-15 實測：連打 300 次，超過門檻的回 429，10 秒後恢復。
- **Bot Fight Mode 刻意不開**：它會把 MacBook 後端（非瀏覽器）的拉單請求當機器人擋掉，免費方案不能設例外。
  灌單由 Turnstile、程式內建上限（docs/44 §8）與上面的速率限制處理。
- Turnstile widget「lukeng-camp.com (Spin)」：網域清單目前含 `localhost`、`127.0.0.1`，**正式上線前請店主刪掉這兩個**。

## 3. MacBook 接上正式雲端（給 MacBook 上的 AI agent）

先完成 docs/83–86 的升級（或直接做最新一份）。然後：

1. 向店主取得三個值（開發機 `.env.online-order-production` 裡的 `ONLINE_ORDER_BASE_URL`、`ONLINE_ORDER_SECRET`、
   `ONLINE_ORDER_STORE_ID`），**用店主指定的安全方式傳遞，不要貼進 repo、文件或聊天紀錄**。
2. 加到 MacBook repo 根目錄的 `.env`（後端啟動時 `source` 的那一份，見 docs/43 §3），三個變數名稱同上；重啟後端（`install-launchd.sh` 或 docs/43 §3 的重啟步驟）。
3. 驗證：
   - 店主用管理者帳號開「餐飲菜單」→ 線上點餐區塊，**不再顯示「尚未設定線上點餐的雲端網址與密鑰」**。
   - 按「發佈到線上點餐」成功；手機開 https://order.lukeng-camp.com 看得到菜單。
   - POS 結帳頁「線上訂單」面板顯示「接單中」與最近連線時間（每 5 秒更新）。
4. 「列印桌號 QR」印出各桌 QR（docs/85），貼到桌上前用手機掃一次確認開得到那一桌。

## 4. 上線前檢查清單

| # | 項目 | 誰 | 狀態 |
|---|---|---|---|
| 1 | 網域 `lukeng-camp.com` | 店主 | ✅ 2026-10-15 |
| 2 | 正式 Turnstile 金鑰 | 店主 | ✅ 2026-10-15；widget 網域清單刪掉 localhost／127.0.0.1 ⬜ |
| 3 | 正式 Worker／D1／R2、自訂網域、HTTPS 設定、速率限制 | 開發 | ✅ 2026-10-15 |
| 4 | POS 惡意輸入（DOM XSS）測試 | 開發 | ✅ 分支 test/online-order-pos-xss |
| 5 | MacBook 升級 docs/83–88 並接上正式雲端（§3） | MacBook agent＋店主 | ⬜ |
| 6 | 真手機掃桌號 QR，現金單走完一輪（送單→POS 帶入→結帳） | 店主 | ⬜ |
| 7 | LINE Pay 正式收款資格與費率；取得後設定 `LINEPAY_*` 並用真手機付一筆小額再退款 | 店主 | ⬜ |
| 8 | Cloudflare 免費額度用到 70% 時在 POS 提醒（Workers 每日 10 萬次請求、D1 讀寫） | 開發 | ⬜ 待做 |
| 9 | 真 Turnstile token 過期／重放、正式環境灌單與保留濫用測試（docs/44 §8.4） | 開發 | ⬜（需正式環境開放後、店主同意再做） |
