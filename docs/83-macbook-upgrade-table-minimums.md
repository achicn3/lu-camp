# 83 — MacBook 正式機：升級到 2026-10-14 版（桌號低消、「不知道點什麼？」）

> **給在 MacBook（店內正式機）上工作的 AI agent：** 這份文件是一項**待你執行的升級任務**。
> 前面的升級說明若還沒做，**直接做這份即可**（`alembic upgrade head` 會一次補齊）。
> 照 docs/43 的「§2 動手前」做完，再做本文 §2、§3。任何一步失敗：**停下來、把錯誤原文給店主看**。一定要在**打烊後**做。

## 1. 這次多了什麼（店主 2026-10-14）

| 變更 | 影響到 | 要做的事 |
|---|---|---|
| 設定 →「內用桌號與出餐單」：每個桌號可設「整筆最低金額」或「每人至少一份」低消（docs/44 §4.1） | 後端＋前端＋雲端 Worker | migration、重啟後端、重新 build 前端 |
| 線上訂單帶用餐人數，POS 顯示「桌號 A1・3 位」 | 後端＋前端 | 同上 |
| 引導推薦標題「不知道喝什麼？」改為「不知道點什麼？」 | 前端＋雲端 Worker | 同上 |

> **有 migration**：新 head **`e4b8c2d6f0a1`**（前版 `d9f3b7a1e5c8`）——`settings` 加 `dine_in_minimums`、
> `online_orders` 加 `party_size`。沒有新套件。
> 雲端 Worker 另有 D1 migration `0010_table_minimums.sql`；正式機**仍先不設定**線上點餐雲端，這次不用動。

## 2. 升級步驟

照 docs/43 §3 的指令依序做。`alembic current` 應顯示 **`e4b8c2d6f0a1 (head)`**。

## 3. 驗證（每一項都回報結果）

1. docs/43 §4 開頭的 `curl`／`launchctl` 檢查：三個 `state` 都是 `running`、兩個 http 都是 `200`。
2. 請**店主**（管理者帳號）打開「設定」→「內用桌號與出餐單」：每個桌號旁有「不設低消」下拉選單（不用真的改）。

## 4. 出問題怎麼退

先退資料庫再退程式：`uv run alembic downgrade d9f3b7a1e5c8`（只刪掉低消設定與線上單人數，不影響帳），
再 `git checkout <記下的 commit>`，照 docs/43 §6 重 build 前端、`install-launchd.sh`。
