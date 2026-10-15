# 88 — MacBook 正式機：升級到 2026-10-16 版（換雲端時照片、字型會重推）

> **給在 MacBook（店內正式機）上工作的 AI agent：** 這份文件是一項**待你執行的升級任務**。
> 前面的升級說明（docs/83–86）若還沒做，**直接做這份即可**（`alembic upgrade head` 會一次補齊；docs/85 的新套件
> 要 `pnpm install`，照 docs/43 §3 的前端步驟就會做到）。照 docs/43 的「§2 動手前」做完，再做本文 §2、§3。
> 任何一步失敗：**停下來、把錯誤原文給店主看**。一定要在**打烊後**做。

## 1. 這次多了什麼（2026-10-16）

| 變更 | 影響到 | 要做的事 |
|---|---|---|
| 發佈線上菜單時，「照片、字型推過了沒」改成分雲端記：接到另一個雲端（例如正式站 order.lukeng-camp.com）時會自動重推 | 後端 | migration、重啟後端 |

> **有 migration**：新 head **`b5e2c8f4a1d7`**（前版 `a3d9e1f7c5b2`）——`online_pushed_media` 加 `cloud` 欄。
> 舊紀錄沒有記雲端，升級後第一次發佈會把照片、字型重推一次（雲端本來就冪等，多推無害）。沒有新套件。

## 2. 升級步驟

照 docs/43 §3 的指令依序做。`alembic current` 應顯示 **`b5e2c8f4a1d7 (head)`**。

## 3. 驗證（每一項都回報結果）

1. docs/43 §4 開頭的 `curl`／`launchctl` 檢查：三個 `state` 都是 `running`、兩個 http 都是 `200`。
2. 已接上正式雲端（docs/87 §3）時，請店主按一次「發佈到線上點餐」：成功，手機開 https://order.lukeng-camp.com 菜單字型、照片正常。

## 4. 出問題怎麼退

先退資料庫再退程式：`uv run alembic downgrade a3d9e1f7c5b2`（推送紀錄併回一份，最多讓下次發佈重推一次），
再 `git checkout <記下的 commit>`，照 docs/43 §6 重 build 前端、`install-launchd.sh`。
