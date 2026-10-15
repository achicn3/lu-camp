# 86 — MacBook 正式機：升級到 2026-10-15 版（線上點餐拿掉備註）

> **給在 MacBook（店內正式機）上工作的 AI agent：** 這份文件是一項**待你執行的升級任務**。
> 前面的升級說明（docs/83、docs/84、docs/85）若還沒做，**直接做這份即可**（`alembic upgrade head` 會一次補齊；
> docs/85 的新套件要 `pnpm install`，照 docs/43 §3 的前端步驟就會做到）。
> 照 docs/43 的「§2 動手前」做完，再做本文 §2、§3。任何一步失敗：**停下來、把錯誤原文給店主看**。一定要在**打烊後**做。

## 1. 這次多了什麼（店主 2026-10-15）

| 變更 | 影響到 | 要做的事 |
|---|---|---|
| 線上點餐的「備註」整個移除：POS 線上訂單清單、帶入結帳的「客人備註」都不再出現（docs/44） | 後端＋前端＋雲端 Worker | migration、重啟後端、重新 build 前端 |

> **有 migration**：新 head **`a3d9e1f7c5b2`**（前版 `f1a7c3e9b2d4`）——刪掉 `online_orders.note` 欄。
> 2026-10-13 起雲端就已拒收有內容的備註，正式機也還沒接線上點餐，這欄沒有資料，刪掉不會少任何東西。沒有新套件。
> 雲端 Worker 另有 D1 migration `0012_drop_order_note.sql`；正式機**仍先不設定**線上點餐雲端，這次不用動。

## 2. 升級步驟

照 docs/43 §3 的指令依序做。`alembic current` 應顯示 **`a3d9e1f7c5b2 (head)`**。

## 3. 驗證（每一項都回報結果）

1. docs/43 §4 開頭的 `curl`／`launchctl` 檢查：三個 `state` 都是 `running`、兩個 http 都是 `200`。
2. 請**店主**在 POS 結帳頁按「線上訂單」：面板打得開、舊的訂單照常顯示。

## 4. 出問題怎麼退

先退資料庫再退程式：`uv run alembic downgrade f1a7c3e9b2d4`（加回空的 `note` 欄，不影響帳），
再 `git checkout <記下的 commit>`，照 docs/43 §6 重 build 前端、`install-launchd.sh`。
