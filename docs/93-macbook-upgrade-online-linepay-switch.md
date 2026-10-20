# 93 — MacBook 正式機：升級到 2026-10-20 版（線上 LINE Pay 開關）

> **給在 MacBook（店內正式機）上工作的 AI agent：** 這份文件是一項**待你執行的升級任務**。
> 前面的升級說明（docs/83–92）若還沒做，**直接做這份即可**（`alembic upgrade head` 會一次補齊）。
> 照 docs/43 的「§2 動手前」做完，再做本文 §2、§3。任何一步失敗：**停下來、把錯誤原文給店主看**。一定要在**打烊後**做。

## 1. 這次多了什麼（2026-10-20）

| 變更 | 影響到 | 要做的事 |
|---|---|---|
| 「餐飲菜單 → 線上發布」多一個勾選「線上點餐開放 LINE Pay」（管理者）：打開才開放客人在手機上用 LINE Pay；**預設關** | 後端、前端 | migration、重 build、重啟 |
| 雲端：沒打開時客人頁 LINE Pay 顯示「即將開放」；關掉只擋新的付款，已經在付的照常完成（雲端已由開發機部署） | 雲端 | 不用做事 |

> **有 migration**：新 head **`e6b2d8f4a3c1`**（前版 `d4a8c2e6f1b3`）——`online_order_links.linepay_enabled`／`linepay_configured`（雲端狀態的快取）。沒有新套件。
> **順序：雲端先上、再升級店內**（新 POS 會呼叫雲端的新開關端點）。

## 2. 升級步驟

照 docs/43 §3 的指令依序做（含前端 build）。`alembic current` 應顯示 **`e6b2d8f4a3c1 (head)`**。

## 3. 驗證（每一項都回報結果）

1. docs/43 §4 開頭的 `curl`／`launchctl` 檢查：三個 `state` 都是 `running`、兩個 http 都是 `200`。
2. 請店主用管理者帳號開「餐飲菜單 → 線上發布」：看得到「線上點餐開放 LINE Pay」，**不要打開**（正式金鑰還沒設定時會顯示「雲端還沒設定 LINE Pay 金鑰，打開也不會生效」）。

## 4. 出問題怎麼退

先退資料庫再退程式：`uv run alembic downgrade d4a8c2e6f1b3`，再 `git checkout <記下的 commit>`，照 docs/43 §6 重 build 前端、`install-launchd.sh`。
