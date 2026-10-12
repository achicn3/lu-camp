# 79 — 線上點餐攻擊驗證與修補（2026-10-13）

本輪由店主授權在測試環境做主動攻擊驗證，範圍為公開點餐 API → 雲端訂單 → 店內同步 → POS／庫存。
工作分支 `test/online-order-security-audit`，基於 `928401f5`，接續 10/12 的付款發起限流／body 上限修補。
正式機沒有部署或測試；本文不是整站或 POS 主機已完成滲透測試的證明。

## 已重現並修補

### 高：一張特殊備註訂單可阻斷 POS 收單

- 公開送單原本只檢查備註型別、60 字上限。JSON 中的 NUL（`\u0000`）能通過 Worker/D1；
  店內匯入時直接放進 PostgreSQL text，造成 `CharacterNotInRepertoireError`，整輪匯入回滾。
  這條攻擊不需要 POS 帳號或整合密鑰，只需成功送出普通訂單。
- 真 PostgreSQL 測試先證明惡意單會阻止後面的正常訂單。孤立 UTF-16 surrogate 也會被 PostgreSQL 拒絕，
  因此一起防禦；不把 surrogate 在每種雲端傳輸中的行為都視為已完成端到端驗證。
- 新單：Worker 以 Unicode 模式拒絕 NUL／孤立 surrogate，回 422，不寫入訂單。正常 emoji 與換行保留。
- 舊單：POS 匯入清除這些無法儲存的備註字元，保留其餘內容，正常匯入並回報；不只是跳過，
  避免毒化單一直留在雲端 NEW 佔據拉單額度。
- 回歸證據：Worker 新增四個拒絕案例修前均收到 201；POS 三個案例修前均在 DB 寫入失敗。
  修後惡意舊單及後續正常單都能匯入／回報，重拉不重複建立。

### 中：重複付款確認可無限呼叫金流

- 未授權交易的公開 confirm 會走 `PENDING → CONFIRMING → PENDING`，原本只限制付款發起無法阻止這條路徑。
- 測試假金流重現：連續八次觸發八次 upstream confirm；並行八次也能多次觸發。
  本輪沒有對真 LINE Pay 服務施加重複請款壓力。
- 修正：以既有 D1 表，按商店／訂單／付款嘗試／30 秒固定區間原子記錄；同區間後續公開請求回 202，
  不再呼叫金流。第一個正常回呼、已付款查詢、POS 補查不受這個公開確認限制。
- 取捨：先收到未授權確認、稍後同區間才真的授權時，客人可能暫見 PENDING，等下一區間或 POS 補查收斂。
  固定區間交界可有兩次相鄰確認；此修補限制濫用量，不保證滑動 30 秒內恆為一次。

## 實際執行結果

| 驗證 | 結果與界限 |
|---|---|
| Worker 全套 | 258/258；包含價格、選項、HMAC、付款、限流、惡意備註 |
| PostgreSQL 整合／併發／簽章 | 53/53；`test_online_orders_store.py`、`test_online_order_outbox_concurrency.py`、`test_onlineorder_signing.py` |
| 靜態檢查 | Worker／客人頁 TypeScript、修改 Python 檔 ruff／mypy、客人頁 build、diff whitespace 通過 |
| 真 Worker/D1 → backend/Postgres | 6 組攻擊驗證通過，見下表；專用資料庫 `lucamp_security_e2e_1013` |
| 獨立審查 | 未發現阻擋提交／staging 部署問題；確認付款可能短暫等待的取捨 |

真連線腳本 `frontend/scripts/online-security-smoke.mjs`：

| 攻擊 | 實測結果 |
|---|---|
| NUL／孤立 surrogate 備註 | 真 HTTP Worker 回 422，不進 POS |
| 負數、零、小數、每行超量 | 回 422 |
| 單價改 1、total 改 1、偽裝 PAID 與交易號 | Worker 及 POS 仍為 150 元／UNPAID／未線上付款 |
| 儲存型 XSS 備註 | 客人真 Chromium 顯示原字串，未建立攻擊 image，未執行 handler；POS API 原樣儲存 |
| SQL 注入備註 | 保留為純文字，PostgreSQL 訂單 API 正常；沒有執行 SQL |
| 兩客搶最後一份 | 一筆 HELD、一筆 REJECTED；庫存 0，店員取消後回到 1 |

客人頁截圖：`/tmp/lukeng-security-shots/guest-xss-text.png`。
本次未跑 POS 前端的惡意備註 DOM 驗證；API 儲存成功不能代替這項測試。

重跑需先準備隔離資料庫、seed 使用者與對應本機 Worker，設定 backend 的線上整合指向該 Worker：

```bash
SMOKE_ALLOW_WRITE=1 SMOKE_API=http://127.0.0.1:8116 SMOKE_ORDER=http://127.0.0.1:8794 \
  node frontend/scripts/online-security-smoke.mjs
```

腳本只接受 HTTP loopback；會建立專用品項／訂單、測試保留與店員取消，不要指向正式資料庫或正式機轉接埠。
訂單取消後保留會釋放；專用測試品項留在隔離庫以便查證。
金流確認濫用測試使用假金流，真連線腳本只建立現金測試單，不扣款／開發票。

## 部署與剩餘工作

- Worker 已部署 **staging**：版本 `a316aba4-782f-4bdd-8293-3e04079a83e5`。
- POS 修正已在上述隔離後端運行；**正式 POS 尚未升級**。雲端部署只能擋新毒單，
  舊毒單恢復能力必須升級 backend 才生效。本次不需要 migration，也不改 API 合約。
- 雲端有限度攻擊腳本：`cd online-order && node test/security-smoke.mjs`；複測 **24/24** 通過。
  部署後首次為 23/24（NUL 測試收到 422，但回應內容／標頭斷言有一項未符）；
  單項重查與整套複測均通過。首次腳本未細分斷言失敗點，原因未定位，已補細分診斷，不能據此宣稱零風險。
- 尚未完成：付款發起重新驗 Turnstile、真 Turnstile token 過期／重放、POS DOM XSS、
  完整 staging 庫存保留濫用／灌單測試、正式部署設定與網路暴露面檢查。
- 目前沒有證據能主張已取得 POS 主機執行權限、員工帳號或資料庫控制權；
  本輪證明的是未授權客人可透過訂單資料阻斷 POS 收單，以及已修補的兩項具體漏洞。
