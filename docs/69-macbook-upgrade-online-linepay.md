# 69 — MacBook 正式機：升級到 2026-10-08 版（線上點餐 LINE Pay 付款）

> **給在 MacBook（店內正式機）上工作的 AI agent：** 這份文件是一項**待你執行的升級任務**。
> 前面的升級說明（docs/45、46、48、51–68）若還沒做，**直接做這份即可**（`alembic upgrade head` 會一次補齊）。
> 照 docs/43 的「§2 動手前」（記版本、確認打烊、先備份、工作目錄乾淨）做完，再做本文 §2、§3。
> 任何一步失敗：**停下來、把錯誤原文給店主看、不要自己猜著修**。一定要在**打烊後**做。

## 1. 這次多了什麼（docs/44 §4.4.2；O5）

| 變更 | 影響到 | 要做的事 |
|---|---|---|
| 客人可在線上點餐用 LINE Pay 付款；付好由開著的 POS 自動成立銷售、出餐單、開發票 | 資料庫＋後端 | migration `d5f1b3c7e9a2` |
| POS「線上訂單」：「等待 LINE Pay 付款」（不能帶入收現金）、「LINE Pay 已付款」自動成立與上方提示 | 前端 | 重新 build 前端 |
| 線上付款的 LINE Pay 作廢／退貨改走交易號退款 | 後端 | 不用另外處理 |

> 沒有新套件。正式機**仍先不設定**線上點餐雲端，所以這些都看不到——這是預期的。

**日後開線上 LINE Pay 時要知道**（不是這次要做）：
- 雲端 Worker 要設 `LINEPAY_CHANNEL_ID`／`LINEPAY_CHANNEL_SECRET`（`wrangler secret put`）與 `LINEPAY_API_BASE`，
  並先套用雲端 D1 migration `0007_linepay`，再部署 Worker。
- **店內 `.env` 也要有同一組 `LINEPAY_CHANNEL_ID`／`LINEPAY_CHANNEL_SECRET`**：線上付款的單作廢／退貨要由店內呼叫
  LINE Pay 退款，沒設定就退不了（畫面會說「LINE Pay 尚未設定」）。
- 正式環境的線上收款資格與費率要店主先向 LINE Pay 確認。

## 2. 升級步驟

照 docs/43 §3 的指令依序做。`alembic current` 應顯示 **`d5f1b3c7e9a2 (head)`**。

## 3. 驗證（每一項都回報結果）

1. docs/43 §4 開頭的 `curl`／`launchctl` 檢查：三個 `state` 都是 `running`、兩個 http 都是 `200`。
2. 請**店主**在 POS 結一筆一般現金交易與（若店裡有開）一筆 LINE Pay 掃碼交易：照常可結。

## 4. 出問題怎麼退

- 只退程式：`git checkout <記下的 commit>`，再照 docs/43 §6 重 build 前端、`install-launchd.sh`。
  （這次只新增欄位，舊程式讀得懂；雲端沒設定時不會有線上付款資料。）
- 降版 `d5f1b3c7e9a2` 在**已有線上 LINE Pay 收款或已付款線上單**時會拒絕——只退程式、不要降資料庫。
