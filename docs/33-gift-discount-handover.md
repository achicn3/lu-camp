# 33 — 贈品與臨時折扣：對抗審查交接筆記

分支 `feat/gift-and-manual-discount`（20 commit，在 `origin/main` 之上）。
功能規格見 [docs/32](./32-gift-and-manual-discount.md)、決策見 [ADR-015](./adr/ADR-015-gift-lines-vs-manual-discount.md)。
本檔只記**審查過程與尚未收斂的事**，供下一個工作階段直接接手。

## 目前狀態

四道門與瀏覽器 E2E 全綠：`ruff` / `mypy`(380) / `pytest` exit 0、0 failed、覆蓋率 88.41% /
`eslint` / `tsc` / `vitest` 408 passed / hardware-agent 155 passed /
`gift-discount-smoke.mjs` 15/15。

## 反覆出現的失誤模式（**接手時請先讀這段**）

八輪對抗審查中，從第四輪起找到的問題**幾乎都是同一類**：

> 改動一個金額口徑或新增一個欄位時，只修了「當下看到的那幾處」，
> 沒有先把**所有讀取／寫入點列出來**再逐一處理。

具體案例：

| 輪次 | 漏在哪 |
|---|---|
| 2 | 退貨毛利報表仍以牌價反轉（正向已改認實付） |
| 3 | `staff_payload` 寫進 DB 卻沒在 API 回傳，等於整個修正沒生效 |
| 4 | 新增 `catalog_cogs` 桶，但趨勢與日報的 COGS 沒納入 |
| 5 | 一般商品成本併進「自有庫存總成本」，卻沒進總售價與庫齡桶 |
| 7 | 發票 full-return 判斷修了 ISSUED 那條路，漏了 PENDING 那條 |
| 8 | replay 補了四處、漏第五處（簽署競態）；後端指紋正規化了、前端沒跟上 |

**建議的做法**：動任何金額口徑前，先 `grep -rn` 出全部使用點（跨 backend／frontend／
hardware-agent 三個服務），列成清單逐一判定，再開始改。第五輪之後我自己這樣掃過一次，
就找到紙本收據印折前小計（客人拿走的那張紙）——那是審查沒點名、我自己抓到的。

## 最優先：正式建店流程未定義（第九輪 high，**合併前或上線前必須解決**）

`ensure_default_reasons()`（佈建贈品／折扣的預設原因）在程式裡**唯一的呼叫者**是
`app/scripts/seed_dev_store.py`——而那支檔案的說明第一行就寫著「**勿在正式環境執行**」。
migration 只替「執行當下已存在」的門市插入原因；全新資料庫是先跑完 migration 才建門市，
所以原因表會是空的 → POS 贈品選單為空 → **贈品功能在新的正式門市完全不可用**。

查證後發現問題更根本：**整個 repo 只有 `seed_dev_store.py` 會建立 `Store` 列**。
也就是說「正式上線要怎麼建立門市」本身沒有定義——不只贈品原因，門市抬頭、統編、
發票字軌都走同一條路。贈品只是第一個被這個缺口絆到的功能。

待裁示的三個選項：

- **A（建議）**：把 `seed_dev_store` 正名為正式 bootstrap。機制其實都在了——它已支援以
  環境變數帶入真統編（見其 docstring），也已呼叫 `ensure_default_reasons()`。
  缺的只是「承認它是正式路徑」並把文件與那句「勿在正式環境執行」改對。改動很小。
- **B**：另做一支正式的建店流程或後台頁面。
- **C**：上線時手動在資料庫建門市與原因，之後再補。

無論選哪個，都應補一條整合測試：**從空資料庫走完 bootstrap 後，直接驗證預設原因存在
且贈品結帳可完成**（現有測試只是直接呼叫 helper，沒有走真正的建店路徑）。

## 尚未處理

1. **舊版 cart-v1 快照的相容轉換**（下次改版前必做）
   本批把客顯快照欄位改為必填。正式上線是全新資料庫、沒有舊快照，故本次無影響；
   但第二次以後的升級，店已營業，升級當下可能有 DRAFT／FROZEN／**PAYMENT_UNCERTAIN**
   購物車在途——後者可能已完成外部扣款，讀不回來就失去 POS 的對帳入口。
   修法：讀取前依 `content_version` 做相容轉換，為 v1 快照補安全預設值。
2. **退貨同意書以兩個時間點的狀態組內容**：逐行金額與總額分別查詢，同一張單若有併發退貨
   可能來自不同快照。單店單機碰不到。
3. **庫存價值匯出的其餘欄位**仍為舊口徑。
4. **紙本收據只有單元測試，沒有實機列印驗證**：排版是否正確需接上 EPSON 印表機實跑。

## 明確不做（裁示）

見 [docs/32 §9](./32-gift-and-manual-discount.md) 的取捨表：主管核准機制、折扣與贈品上限、
`Σ net_amount` 的 DB 守衛、`NORMAL net_amount > 0` 的 CHECK、一般商品成本併入自有庫存總成本。
每一項都附了「為什麼不做」與代價。

## 合併前的檢查清單

1. `git fetch && git rebase origin/main`
2. 四道門（後端 `ruff`/`mypy`/`pytest`、前端 `eslint`/`tsc`/`vitest`、hardware-agent、
   OpenAPI 重生後無漂移）
3. 瀏覽器 E2E `frontend/scripts/gift-discount-smoke.mjs`（真 backend + 真 Postgres）
4. Codex 審查無重大意見
5. 先推 feature 分支 → `--ff-only` 合併 → 推 `main`
