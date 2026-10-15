# 85 — MacBook 正式機：升級到 2026-10-15 版（桌號 QR 一鍵列印）

> **給在 MacBook（店內正式機）上工作的 AI agent：** 這份文件是一項**待你執行的升級任務**。
> 前面的升級說明（docs/83、docs/84）若還沒做，**直接做這份即可**（`alembic upgrade head` 會一次補齊）。
> 照 docs/43 的「§2 動手前」做完，再做本文 §2、§3。任何一步失敗：**停下來、把錯誤原文給店主看**。一定要在**打烊後**做。

## 1. 這次多了什麼（店主 2026-10-15）

| 變更 | 影響到 | 要做的事 |
|---|---|---|
| 菜單頁「線上點餐」多一個「列印桌號 QR」：每桌一張卡，A4 一頁六張（docs/44 §4.1） | 前端**新套件** `qrcode-generator`＋後端 API 多回店名 | **一定要 `pnpm install`**，否則前端 build 會失敗；重啟後端 |

> **沒有新的 migration**（docs/84 的 `f1a7c3e9b2d4` 仍是 head）。雲端 Worker 沒有變。

## 2. 升級步驟

照 docs/43 §3 的指令依序做，**前端那一步不能省略 `pnpm install`**（新套件 `qrcode-generator`）。
`alembic current` 應顯示 **`f1a7c3e9b2d4 (head)`**。

## 3. 驗證（每一項都回報結果）

1. docs/43 §4 開頭的 `curl`／`launchctl` 檢查：三個 `state` 都是 `running`、兩個 http 都是 `200`。
2. 請**店主**（管理者帳號）打開「餐飲菜單」→ 線上點餐區塊：若已設定線上點餐雲端，會看到「列印桌號 QR」按鈕；
   正式機**還沒設定雲端**時不會出現這個按鈕，屬正常。

## 4. 出問題怎麼退

這次沒有 migration：`git checkout <記下的 commit>`，照 docs/43 §6 重 build 前端（含 `pnpm install`）、`install-launchd.sh`。
