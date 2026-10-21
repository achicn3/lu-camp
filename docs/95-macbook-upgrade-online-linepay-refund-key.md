# 95 — MacBook 正式機：升級到 2026-10-21 版（線上 LINE Pay 退款用線上通路的金鑰）

> **給在 MacBook（店內正式機）上工作的 AI agent：** 這份文件是一項**待你執行的升級任務**。
> 前面的升級說明（docs/83–94）若還沒做，**直接做這份即可**（`alembic upgrade head` 會一次補齊）。
> 照 docs/43 的「§2 動手前」做完，再做本文 §2、§3。任何一步失敗：**停下來、把錯誤原文給店主看**。一定要在**打烊後**做。

## 1. 這次多了什麼（2026-10-21）

| 變更 | 影響到 | 要做的事 |
|---|---|---|
| 客人在線上點餐用 LINE Pay 付的單，作廢／退貨時改用**線上點餐那組** LINE Pay 金鑰退款（之前誤用店內掃碼那組，平台會找不到交易、退不了） | 後端 | 重啟；`.env` 新增兩個設定（見下） |

> 沒有新 migration、沒有新套件。

**店內 `.env` 的 LINE Pay 金鑰從此分兩組**（取代 docs/69 §1「店內 `.env` 也要有同一組」的說法）：

| 設定 | 是哪一組 | 用在 |
|---|---|---|
| `LINEPAY_CHANNEL_ID`／`LINEPAY_CHANNEL_SECRET` | 店內掃碼（Offline API） | 掃客人付款碼收款、退店內掃碼的單 |
| `LINEPAY_ONLINE_CHANNEL_ID`／`LINEPAY_ONLINE_CHANNEL_SECRET` | 線上點餐（Online API），**和雲端 Worker 同一組** | 只用來退客人在線上付的款 |
| `LINEPAY_API_BASE` | 兩組共用 | 正式 `https://api-pay.line.me` |

- **正式的線上金鑰還沒拿到前，`LINEPAY_ONLINE_*` 先不要加**——沒有線上 LINE Pay 收款就不需要退款。
  拿到後由**店主本人**在 MacBook 上把兩行加進 `.env`（金鑰不要貼在對話或寫進 repo），再重啟後端。
- 沒設線上金鑰時，作廢／退貨線上付款的單會被擋下並顯示「線上點餐的 LINE Pay 金鑰尚未設定」；
  不會誤用店內那組。

## 2. 升級步驟

照 docs/43 §3 的指令依序做（含前端 build）。

## 3. 驗證（每一項都回報結果）

1. docs/43 §4 開頭的 `curl`／`launchctl` 檢查：三個 `state` 都是 `running`、兩個 http 都是 `200`。
2. 有設 `LINEPAY_ONLINE_*` 時，回報 `grep -c '^LINEPAY_ONLINE_' .env` 的數字（應為 2；**不要印出內容**）。

## 4. 出問題怎麼退

`git checkout <記下的 commit>`，照 docs/43 §6 重 build 前端、`install-launchd.sh`。
