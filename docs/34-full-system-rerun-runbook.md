# 34 — 全系統重跑 Runbook（重置 → 43 支手冊腳本 → 真開發票 → 重建手冊）

一次把整個系統從空資料庫走完，順便重建操作手冊。**照著這份從頭做即可，不需要前文脈絡。**

裁示（2026-08-03）：資料庫**重置**、Amego **用測試憑證真的開票**、EPSON **真的列印**。

---

## 0. 前置

- Postgres 容器 `lu-camp-db-1` 已起（`127.0.0.1:1234`，帳密 `lucamp` / `lucamp_dev_pw`）
- EPSON TM-T82III 已開機且在網路上（IP 見 `hardware-agent/.env` 的 `AGENT_EPSON_HOST`）
- Brother QL-810W **若未接**，標籤列印步驟會失敗——屬預期，記錄下來即可

```bash
cd /home/test/lu-camp
source frontend/scripts/setup-browser-e2e.sh          # 冪等；裝 chromium 缺的系統庫與中文字型
export LD_LIBRARY_PATH="$LU_CAMP_PW_LDPATH:$LD_LIBRARY_PATH"
```

> 沒有這個 `LD_LIBRARY_PATH`，Playwright 會起不來；沒裝字型，截圖中文會變方框。

---

## 1. 重置資料庫

```bash
DOCKER="/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe"
"$DOCKER" exec lu-camp-db-1 psql -U lucamp -d postgres \
  -c "DROP DATABASE IF EXISTS lucamp_manual" -c "CREATE DATABASE lucamp_manual"
```

**注意**：`pytest` 會 `drop_all` 它指到的資料庫。本 runbook 全程使用 `lucamp_manual`，
不要在同一個 shell 把 `DATABASE_URL` 留著去跑 pytest。

---

## 2. Migration 與 seed（統編要用 Amego 測試帳號的 12345678）

```bash
cd /home/test/lu-camp/backend
export DATABASE_URL=postgresql+asyncpg://lucamp:lucamp_dev_pw@127.0.0.1:1234/lucamp_manual
export APP_ENV=development
export SECRET_KEY="dev0secret0key0do0not0use0in0prod0000000000000000000000000000"
export PII_ENC_KEY="hKq2EfqmY84r6zuGQj4/fqFjn4DWIpzSkv+b5wYzh/k="
export HMAC_KEY="eeacaeb328e7afd580365221418c386e1fc80b0b5e2d7025e38fd430cc8edf2b"
export CORS_ORIGINS="http://localhost:3000"

uv run alembic upgrade head

# **統編必須是 12345678**：Amego 測試帳號綁這個統編，不符會被平台直接回拒、開不出發票。
SEED_STORE_TAX_ID=12345678 SEED_STORE_NAME="測試環境有限公司" \
  uv run python -m app.scripts.seed_dev_store
ALLOW_DEV_SEED=true SEED_USER_PASSWORD=dev-test-123456 \
  uv run python -m app.scripts.seed_dev_user
ALLOW_DEV_SEED=true uv run python -m app.scripts.seed_dev_consignment
```

`seed_dev_store` 會一併佈建贈品／折扣的預設原因（`ensure_default_reasons`）——
**沒有這步，POS 的贈品選單會是空的、贈品完全不能用**。

---

## 3. 起三個服務

```bash
# 後端 :8000（指向 lucamp_manual；沿用第 2 節已 export 的環境變數）
cd /home/test/lu-camp/backend
nohup uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/be.log 2>&1 &

# 前端 :3000
cd /home/test/lu-camp/frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 nohup pnpm exec next dev -p 3000 > /tmp/fe.log 2>&1 &

# 硬體代理 :8787（**必須載入 .env 才會用真實驅動**，否則 driver=fake、不會真的印）
cd /home/test/lu-camp/hardware-agent
set -a && . ./.env && set +a
nohup uv run uvicorn agent.main:app --host 127.0.0.1 --port 8787 > /tmp/agent.log 2>&1 &

# 確認：driver 必須是 real
curl -s http://127.0.0.1:8787/devices/status | python3 -m json.tool | grep -E '"id"|"driver"'
```

三個都要 200 才往下：`:8000/api/v1/health`、`:3000/login`、`:8787/devices/status`。

---

## 4. 跑 43 支手冊腳本

```bash
cd /home/test/lu-camp/frontend
export LD_LIBRARY_PATH="$LU_CAMP_PW_LDPATH:$LD_LIBRARY_PATH"
export MANUAL_ALLOW_EINVOICE_ISSUE=true   # ← 真的開發票（本次為 Amego 測試憑證，裁示允許）

for s in 01-shell 02-cash-open 02b-cash-validation 03-contacts 04-kiosk-pair \
         05-acquisition 06-inventory 07-menu-campaigns \
         08-pos 08b-pos-mixed 08c-pos-consignment 08d-pos-mixed-cash \
         08e-pos-mixed-linepay 08f-pos-gift-discount \
         09-sales 09b-sales-void 09c-invoice-return-void 09d-invoice-disposition \
         10-consignment 11-purchasing 12-stocktake-signing 13-signing \
         14-reports 14b-reports-reconciliation 14c-reports-gift-discount \
         15-settings-backup 15b-settings-premium 16b-backup-error \
         17-einvoice-linepay 18-cash-close; do
  echo "=== $s"
  node scripts/manual/$s.mjs 2>&1 | tail -5
done
```

**順序不可調換**：後面的腳本會讀前面產生的 `data.json`（例如 POS 要用 05-acquisition 建的商品）。

`04-kiosk-pair` 會產生登入權杖檔，之後的腳本都靠它；**跑完整套後務必執行 `99-cleanup.mjs` 刪除**。

中途失敗處理：記下是哪一支、貼出錯誤，**不要跳過繼續跑**（後面的會連鎖失敗，判讀不出真正的原因）。

---

## 5. Amego 後台截圖（人工）

第 4 節會開出真的（測試環境）發票。到後台找出來截圖，補進手冊的電子發票章節。

- 網址：<https://invoice.amego.tw/>
- 帳號：`test@amego.tw` ／ 密碼：`12345678`
- 統編：`12345678`（公司：測試環境有限公司）

> 這是 **Amego 官方公開的共用測試帳號**，不是本店的機密。
> **絕對不要把正式憑證或正式後台帳密寫進這份文件或 repo 任何地方。**

要截的畫面：發票列表（看得到本次開出的號碼）、單張發票明細、以及若有折讓／作廢的那幾張。
截圖存到 `~/tmp/lu-camp-manual/shots/17-einvoice-amego/`，命名 `01-…`、`02-…`。

---

## 6. 重建手冊

```bash
cd /home/test/lu-camp/frontend/scripts/manual
export LD_LIBRARY_PATH="$LU_CAMP_PW_LDPATH:$LD_LIBRARY_PATH"

# 若新增了 Amego 後台截圖，先把它們加進 make-manifest.mjs 的 PICK 清單
node make-manifest.mjs
node convert-images.mjs ~/tmp/lu-camp-manual/shots/manifest.json ~/tmp/lu-camp-manual/images.json
node build-manual.mjs      # 缺圖必須為 0
node qa-manual.mjs         # 目標 26/26
```

手冊輸出：`~/tmp/lu-camp-manual/露營二手POS-系統操作手冊.html`（單一檔案、圖片內嵌）。

---

## 7. 收尾

```bash
node /home/test/lu-camp/frontend/scripts/manual/99-cleanup.mjs   # 刪除登入權杖檔（必做）
```

停掉三個服務（`pkill -f "uvicorn app.main"`、`pkill -f "next dev"`、`pkill -f "uvicorn agent.main"`）。

---

## 8. 這次重跑要特別核對的事

本輪剛完成「贈品與臨時折扣」（見 [docs/32](./32-gift-and-manual-discount.md)），請重點確認：

1. **POS**：贈品標記、金額摘要（商品折扣／整單折扣／贈品價值分列）、折扣清單可移除
2. **紙本明細聯**：小計印**實付**、折扣有子列、贈品有「★ 贈品」列、中文未截斷
3. **客顯**：小計為實付、贈品看得出是贈品、折扣有說明
4. **退貨**：打過折的商品退**實付**；退主商品未退贈品時會擋下並要求說明
5. **發票**：同月整筆退貨走**作廢**（非折讓）——到 Amego 後台確認該張確實是作廢
6. **報表**：臨時折扣與贈品兩個分頁有數字；銷售毛利有一般商品成本與貢獻毛利

## 9. 已知會失敗／跳過的項目

- **Brother QL-810W 未接** → 標籤列印步驟失敗（預期）
- `08-pos.mjs` 的台灣Pay 步驟先前逾時過（等不到 `.pos-payment-confirm`）；
  該支前 14 張截圖都會正常產生，屬既有環境問題、與本輪改動無關
