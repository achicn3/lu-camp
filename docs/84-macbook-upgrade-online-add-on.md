# 84 — MacBook 正式機：升級到 2026-10-15 版（線上點餐「我是加點」、重新付款要驗證）

> **給在 MacBook（店內正式機）上工作的 AI agent：** 這份文件是一項**待你執行的升級任務**。
> 前面的升級說明（例如 docs/83）若還沒做，**直接做這份即可**（`alembic upgrade head` 會一次補齊）。
> 照 docs/43 的「§2 動手前」做完，再做本文 §2、§3。任何一步失敗：**停下來、把錯誤原文給店主看**。一定要在**打烊後**做。

## 1. 這次多了什麼（店主 2026-10-15）

| 變更 | 影響到 | 要做的事 |
|---|---|---|
| 低消桌點不夠時，客人可勾「我是加點」直接送單；POS 線上訂單標「桌號 A1・加點」，由店員現場判斷（docs/44 §4.1） | 後端＋前端＋雲端 Worker | migration、重啟後端、重新 build 前端 |
| LINE Pay 付款取消／失敗後重新付款，要再做一次人機驗證；付款中按鈕會鎖住（docs/44 §8.4） | 雲端 Worker | 正式機這次不用動 |

> **有 migration**：新 head **`f1a7c3e9b2d4`**（前版 `e4b8c2d6f0a1`）——`online_orders` 加 `add_on`（預設 false）。沒有新套件。
> 雲端 Worker 另有 D1 migration `0011_order_add_on.sql`；正式機**仍先不設定**線上點餐雲端，這次不用動。

## 2. 升級步驟

照 docs/43 §3 的指令依序做。`alembic current` 應顯示 **`f1a7c3e9b2d4 (head)`**。

## 3. 驗證（每一項都回報結果）

1. docs/43 §4 開頭的 `curl`／`launchctl` 檢查：三個 `state` 都是 `running`、兩個 http 都是 `200`。
2. 請**店主**在 POS 結帳頁按「線上訂單」：面板打得開、舊的訂單照常顯示（沒有加點的單不會出現「加點」字樣）。

## 4. 出問題怎麼退

先退資料庫再退程式：`uv run alembic downgrade e4b8c2d6f0a1`（只刪掉線上單的加點標記，不影響帳），
再 `git checkout <記下的 commit>`，照 docs/43 §6 重 build 前端、`install-launchd.sh`。
