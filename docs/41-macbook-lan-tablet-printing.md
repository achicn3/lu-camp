# 41 — MacBook 正式機：讓區網平板也能列印（硬體代理對區網開放）

> **給在 MacBook（店內正式機）上工作的 AI agent：** 這份文件是一項**待你執行的設定任務**。
> 店主已確認問題與修法（2026-09-23），請照「§3 動手前先確認」→「§4 設定步驟」→「§5 驗證」做完，
> 並把 §5 每一項的實際結果回報給店主。遇到文件沒寫到的狀況，先停下來問，不要猜。

## 1. 症狀

多台平板同時收購時，**只有跑伺服器的那台 MacBook 印得出標籤**；平板按列印沒有反應、
或顯示「無法連線硬體代理（請確認櫃檯設備服務）」。收據、發票證明聯、開錢櫃也是同一條路徑，
所以同樣只有 MacBook 本機能用。

## 2. 原因（已查碼確認）

列印**不經過後端**：瀏覽器直接呼叫 MacBook 上的硬體代理（`hardware-agent`，port 8001），
代理再把資料送到印表機（見 `frontend/lib/agent.ts`、`hardware-agent/agent/main.py`）。
目前有三個設定只讓 MacBook 自己連得到代理：

| # | 設定 | 現況 | 為什麼平板不行 |
|---|---|---|---|
| 1 | `frontend/.env.local` 的 `NEXT_PUBLIC_AGENT_URL` | `http://localhost:8001`（或沒設） | 在平板上 `localhost` 指平板自己 |
| 2 | 代理啟動參數 | `--host 127.0.0.1`（docs/37 的寫法） | 代理只收本機連線 |
| 3 | `hardware-agent/.env` 的 `AGENT_CORS_ORIGINS` | 只有 `http://localhost:3000` | 平板的網頁來源是 `http://<Mac IP>:3000`，瀏覽器會擋下，代理端**連一行日誌都不會有** |

另外 macOS 防火牆可能擋住 8001 的外來連線。

## 3. 動手前先確認（不要跳過）

0. **前端與後端必須是同一個版本**：§4.6 要重新 build 前端，build 的是**目前 checkout 的程式碼**。
   若你為了讀這份文件把 repo 切到最新的 `main`，而後端還在跑舊版，build 出來的前端會跟後端對不上。
   先記下正式機現在跑的 commit（`git log -1 --oneline`），再問店主要走哪一條：
   - **只做這項設定、不升級**：留在現在的 commit，用 `git fetch && git show origin/main:docs/41-macbook-lan-tablet-printing.md`
     讀本文，設定改完就在**現在的 commit** 上 build。
   - **順便升級到最新版**：先備份資料庫，打烊後再做；停後端 → `git pull` → `cd backend && uv run alembic upgrade head`
     → 啟動後端 → 再做本文 §4。（散裝販售籃的 migration 退版會遺失資料：出問題只退程式碼、不要降 schema，見 ADR-025。）

1. **這台 Mac 的區網 IP**：`ipconfig getifaddr en0`（有線網路可能是 `en1`）。下文以 `<MAC_IP>` 代稱。
2. **平板現在怎麼連後端**：看 `frontend/.env.local` 的 `NEXT_PUBLIC_API_BASE_URL`。
   平板收購本身能用，代表它應該已經是 `http://<MAC_IP>:8000`——代理位址要用**同一個 IP**。
   若它也是 `localhost`，先停下來問店主（那代表平板連後端的方式跟本文假設不同）。
3. **四個服務現在是怎麼啟動的**（Postgres、backend :8000、frontend :3000、hardware-agent :8001）：
   找 `~/Library/LaunchAgents/*.plist`、`launchctl list | grep -i lucamp`、啟動腳本或終端機指令。
   **改設定要改在實際啟動它的地方**，否則下次開機就還原了。
4. **前端是 `next build` + `next start` 還是 `next dev`**：`NEXT_PUBLIC_*` 在 build 時寫死進 JS，
   改了 `.env.local` 之後正式模式必須**重新 build**，只重啟不會生效。
5. **代理不會自己讀 `.env`**（`agent/main.py` 只讀環境變數）。確認現在的啟動方式怎麼把
   `hardware-agent/.env` 的值帶進去（`set -a; . ./.env; set +a`，或 plist 的 `EnvironmentVariables`）。
   沒帶進去時代理會因 `AGENT_DEVICES` 未設而拒絕啟動；若只帶了一部分、或 `AGENT_DEVICES=fake`，
   該裝置會是假裝模式——回報「印成功」卻不出紙（`/devices/status` 的 `driver` 會是 `fake`）。

### 安全前提（要先跟店主確認）

代理**沒有任何登入驗證**，而且除了列印還能**打開錢櫃**。對區網開放後，連上同一個 Wi-Fi 的
任何裝置都能叫它列印、開錢櫃。動手前請跟店主確認：

- 客人用的 Wi-Fi 是**獨立的訪客網路**，跟店務裝置（Mac、平板、印表機）隔開；
- 路由器**沒有**把 8001（或任何 port）轉發到網際網路。

店主沒確認前不要開放。（長期作法是讓列印改經後端轉送、走登入驗證，另案處理。）

## 4. 設定步驟

1. **Mac 固定 IP**：請店主在路由器用 DHCP 依 Mac 的 MAC 位址綁定固定 IP（印表機已經這樣做）。
   IP 一變，平板又會全部印不出來。
2. **前端代理位址** — `frontend/.env.local`：
   ```
   NEXT_PUBLIC_AGENT_URL=http://<MAC_IP>:8001
   ```
3. **代理允許的來源** — `hardware-agent/.env`（保留 localhost，Mac 本機的瀏覽器還要用）：
   ```
   AGENT_CORS_ORIGINS=http://localhost:3000,http://<MAC_IP>:3000
   ```
   若平板是用主機名稱（例如 `http://lucamp.local:3000`）開網頁，也要加進來——
   **來源必須跟平板網址列上看到的一字不差**（含 port）。
4. **代理改為接受區網連線**：在實際啟動代理的地方把 `--host 127.0.0.1` 改成 `--host 0.0.0.0`：
   ```
   uv run uvicorn agent.main:build_app --factory --host 0.0.0.0 --port 8001
   ```
5. **macOS 防火牆**：系統設定 → 網路 → 防火牆。若有開啟，允許代理的 Python／uvicorn 接受外來連線
   （或暫時確認關閉防火牆時平板能印，以區分是不是防火牆的問題）。
6. **重新 build 並重啟**（**打烊、沒有平板在用時做**）：
   `next build` 會改寫正在服務的 `.next` 目錄，前端還開著就 build，平板會在中途壞掉；
   build 失敗時舊的輸出也可能已經被動過。所以順序是：
   1. 依 §3.3 找到的方式**先停 frontend**（launchd 用 `launchctl bootout` 或 unload）。
   2. build：
      ```
      cd frontend && pnpm build   # 會印出「建置位址：NEXT_PUBLIC_AGENT_URL=...」，確認不是 localhost
      ```
   3. **build 成功** → 啟動 frontend，並重啟 hardware-agent（launchd 用 `launchctl kickstart -k` 或 unload/load）。
   4. **build 失敗** → 不要啟動半套輸出。把 `frontend/.env.local` 改回原值、在原本的 commit 上再 build 一次
      讓店能照常營業，然後把錯誤訊息回報店主，不要自行嘗試其他改法。

## 5. 驗證（每一項都要實際做，回報結果）

在 Mac 上：

```
curl -s http://<MAC_IP>:8001/devices/status          # 要回 JSON，driver 全是 real
curl -s -o /dev/null -D - -H "Origin: http://<MAC_IP>:3000" \
     http://<MAC_IP>:8001/devices/status | grep -i access-control-allow-origin
                                                     # 要看到 http://<MAC_IP>:3000
```

在**平板**上：

1. 瀏覽器開 `http://<MAC_IP>:8001/devices/status`——看得到 JSON，代表網路與防火牆通了。
2. 重新整理收購頁（要載入新 build 的 JS），收一件測試品 → 標籤應自動印出。
3. 至少兩台平板各試一次；再用 Mac 本機試一次，確認本機沒被改壞。
4. 測完把測試收購作廢（收購完成畫面的「這筆有誤？作廢收購」，限管理者）。

## 6. 疑難排解

| 症狀 | 多半是 |
|---|---|
| 平板開 `http://<MAC_IP>:8001/devices/status` 連不上 | 代理還是 `127.0.0.1`、防火牆擋、或 IP 不對 |
| 上一項看得到 JSON，但收購頁列印仍失敗、代理沒有任何日誌 | CORS：`AGENT_CORS_ORIGINS` 沒列到平板網址列的來源（大小寫、port、主機名都要一樣） |
| 列印顯示成功但沒出紙 | 代理在假裝模式：`AGENT_DEVICES` 不是 `real`、或那台印表機的 host 沒帶進啟動環境（`/devices/status` 的 `driver` 是 `fake`） |
| 改了 `.env.local` 沒效果 | 前端沒有重新 `pnpm build`，或平板沒重新整理頁面 |
| 過幾天又不能印 | Mac 的 IP 變了，回到 §4.1 綁固定 IP |

相關：`docs/37-manual-production-plan.md`（代理的 `.env` 與 driver 檢查）、
`hardware-agent/.env.example`（各印表機 IP 與 CORS 說明）。
