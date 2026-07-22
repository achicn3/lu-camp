# 19 — 營運報表前置設計與高風險審查清單

> 狀態：**R0–R6 與前端報表已完成並合併 `main`**。本文件現作為報表口徑、匯出規則與
> 高風險 review checklist；「F6.5 後建議順序」保留歷史施工脈絡，不代表尚未實作。

## 1. 範圍與順序

### 1.1 當時規劃階段先不做

- 不在 F6.5 合併前新增 migration、重生 OpenAPI/generated client、改 `shared/enums.py`。
- 不在本文件階段決定購物金 flows 報表沖正語意。該缺口獨立成「購物金報表沖正一致性」task，與一般報表工作一起處理。
- 不把報表寫入邏輯塞進 sales / inventory / consignment / cashdrawer；報表模組只讀取既有資料。

### 1.2 F6.5 後施工順序（已完成）

1. **R0：購物金報表沖正一致性**
   - 補齊 `/reports/store-credit/liability`、`flows` 對 REVERSAL 的一致語意。
   - 同時處理 acquisition rollback 與既有 SALE_VOID restoration，不只修單一 where 條件。
   - 驗收：被沖正的 CREDIT 不再 aging；被作廢的 SALE DEBIT 不再被 flows 當成永久兌付；CSV/XLSX 與 JSON 一致。

2. **R1：每日現金對帳報表**
   - 讀 `cash_sessions`、`cash_movements`、sale tender 現金部分。
   - 驗收：報表 expected = `opening_float + SALE_IN + ACQUISITION_VOID_IN - BUYOUT_OUT - CONSIGNMENT_PAYOUT_OUT ± MANUAL_ADJUST`；與關帳 `expected_amount` 公式同源（含 F6.5 作廢收購退現 `ACQUISITION_VOID_IN`，不可漏算）。

3. **R2：銷售 / 毛利報表**
   - 讀 `sales`、`sale_lines`、serialized/bulk/consignment 成本與抽成。
   - 驗收：買斷品毛利 = 售價 - 成本；寄售只認抽成；成本未知的 catalog 商品不得假造毛利。

4. **R3：庫存價值與庫齡報表**
   - 讀 serialized / bulk / catalog 庫存狀態與入庫時間。
   - 驗收：已售/已退場不入在庫價值；bulk 以剩餘件數分攤成本；catalog 成本未建模前標示 N/A。

5. **R4：寄售應付報表**
   - 讀 consignment settlements。
   - 驗收：只計 `PENDING` 應付；`PAID`、`CANCELLED`、未來 reclaim 類狀態需明確分欄，不混入待付。

## 2. 報表 v1 設計

### 2.1 共通規則

- **唯讀**：所有 report service 不 commit、不寫 audit、不改來源資料。
- **金額**：使用 `Decimal`，輸出沿用字串整數元。
- **日期區間**：API 使用 `[from, to)` 半開區間；`to <= from` 回 422。
- **時區**：時間瞬間以 UTC 儲存／回傳，API `from/to` 必須帶 offset；營業日與
  day/week/month/quarter 分桶固定以 `Asia/Taipei` 切界線。CSV/XLSX 人讀時間輸出
  `+08:00`，不得受執行主機或 PostgreSQL session 時區影響。
- **店別範圍**：由 token 的 `store_id` 限定；不可用 query 任意指定他店。
- **匯出一致性**：JSON、CSV、XLSX 的數字須同源，CSV/XLSX 只做呈現轉換。
- **CSV/XLSX 安全**：沿用 `reports.export._safe_cell`，防 spreadsheet formula injection。
- **跨表驗證**：每個報表測試都要有「底層交易交叉驗證」，不可只 snapshot response。

### 2.2 R1 每日現金對帳

建議端點：

```text
GET /api/v1/reports/daily-cash?date=YYYY-MM-DD&format=json|csv|xlsx
```

資料來源：

- `cash_sessions`：開帳、關帳、實點、variance。
- `cash_movements`：`SALE_IN`、`ACQUISITION_VOID_IN`、`BUYOUT_OUT`、`CONSIGNMENT_PAYOUT_OUT`、`MANUAL_ADJUST`（F6.5 後 `ACQUISITION_VOID_IN` 為進帳，須計入 expected）。
- `sale_tenders` / `sales`：用於交叉驗證 `SALE_IN` 是否只等於 CASH tender，不含購物金。

欄位：

- session id、opened/closed time、opened_by、closed_by。
- opening_float、cash_sales、acquisition_void_in、buyout_out、consignment_payout_out、manual_adjust_total。
- expected_amount、counted_amount、variance。
- store_credit_redeemed_display_only：當日購物金兌付總額，只展示，不進 expected。

必測：

- 無開帳日回空報表，不回 500。
- 多 session 日依 session 分列，再給 daily total。
- mixed tender 只把 CASH leg 計入 cash_sales。
- store-credit-only sale 不產生 cash movement，也不影響 expected。
- 作廢收購（F6.5）當日 `ACQUISITION_VOID_IN` 計入 expected，與關帳一致；不可漏算，也不可誤併進 cash_sales。

### 2.3 R2 銷售 / 毛利

建議端點：

```text
GET /api/v1/reports/sales-margin?from=&to=&group_by=day|category|item&format=json|csv|xlsx
```

資料來源：

- `sales`、`sale_lines`、`sale_tenders`。
- `serialized_items.acquisition_cost`。
- `bulk_lots.acquisition_cost / total_qty`，乘售出 qty。
- `consignment_settlements.commission_amount` 或同源計算。
- `catalog_products`：現況缺進貨成本；採購/補貨完成前，catalog 毛利欄為 N/A。

欄位：

- gross_sales、cash_received、store_credit_redeemed。
- owned_cogs、bulk_cogs、consignment_commission_income、gross_margin。
- unknown_cost_sales：成本未建模的銷售額，避免毛利被誤讀。

必測：

- 買斷 serialized：毛利 = line_total - acquisition_cost。
- bulk：COGS = round/report policy 明確的 per-piece cost × qty；總剩餘價值不可因四捨五入漂移未說明。
- consignment：收入只認 commission，不把 gross 全額當毛利。
- voided sale 不計入營收與毛利；若未來 returns/allowance 上線，分欄顯示。

### 2.4 R3 庫存價值與庫齡

建議端點：

```text
GET /api/v1/reports/inventory-value?aging=true&format=json|csv|xlsx
```

資料來源：

- `serialized_items`：`IN_STOCK` 且 owned 才用 acquisition_cost 計價；consignment 分開列示，不算自有庫存價值。
- `bulk_lots`：`ON_SALE` 且 remaining_qty > 0，以剩餘件數分攤成本。
- `catalog_products`：採購成本未建模前，列 quantity_on_hand 與零售價，不列成本價值。

欄位：

- item_kind、count、retail_value、cost_value、age bucket。
- buckets：<30 / 30-90 / 90-180 / 180-365 / >365 days。
- consignment_inventory_gross：寄售在庫售價總額，另列，不當作自有資產。

必測：

- SOLD / SOLD_OUT / RETURNED_TO_CONSIGNOR / WRITTEN_OFF 不入在庫價值。
- bulk remaining_qty=0 不入價值。
- 跨店資料不可出現在本店報表。

### 2.5 R4 寄售應付

建議端點：

```text
GET /api/v1/reports/consignment-payables?status=PENDING|PAID|ALL&format=json|csv|xlsx
```

資料來源：

- `consignment_settlements`。
- contacts：寄售人姓名、電話；不可輸出 national_id 明文。
- sales / serialized_items：售出日期、品名、sale id。

欄位：

- consignor_id、consignor_name、settlement_id、sale_id、item_code、gross、commission_amount、payout_amount、status。
- total_pending_payout、total_paid、total_cancelled。

必測：

- 只計 `PENDING` 到待付合計。
- 已付款 settlement 不重複出現在 pending。
- 未來若退貨已付款形成 reclaim_needed，必須獨立分欄，不能負數沖掉 pending。

## 3. 購物金報表沖正一致性 task

此 task 先於一般報表或與 R1 同 branch 前置完成。

### 3.1 需要先定義的語意

- `liability` / aging：被 REVERSAL 沖正的 CREDIT 不可作為未兌付發出批次。
- `flows.issued`：期間 CREDIT 若已被 acquisition rollback 沖正，是否顯示 gross issued + reversal，或直接顯示 net issued，需固定一種語意。
- `flows.redeemed`：SALE DEBIT 若 sale void 已 REVERSAL 入回，是否顯示 gross redeemed + reversal，或直接顯示 net redeemed。
- `net_change` 必須等於期間內帳本 signed_amount 淨變化，且能與 liability 差額對上。

### 3.2 建議採用語意

- 報表列出 `issued_gross`、`issued_reversed`、`issued_net`。
- 報表列出 `redeemed_gross`、`redeemed_reversed`、`redeemed_net`。
- 現有 `issued` / `redeemed` 若保留，定義為 net 欄位；CSV 補 gross/reversed 欄以便稽核。
- `net_change = issued_net - redeemed_net + manual_adjustment_net`，若 adjustment 也要納入，需明確分欄。

### 3.3 必測

- acquisition credit 後 rollback：liability=0、aging=0、flows issued_net=0。
- sale debit 後 sale void：redeemed_net=0、liability 回到原餘額。
- 同期間與跨期間沖正都要測：gross/reversed 可落在不同日期，但帳本淨變化每期要能解釋。

## 4. 高風險 adversarial review 清單

以下任務一律用 `/codex:adversarial-review --base main`，重點不是風格，而是找 race、rollback、跨店、金額與 lock-order 問題。

### 4.1 共通清單

- **交易邊界**：所有副作用是否同一 DB transaction；失敗是否 rollback，不留 header-only / movement-only / stock-only。
- **鎖順序**：同一流程若碰 inventory、cash session、store credit account、settlement，是否有全專案一致順序；是否存在 AB-BA deadlock。
- **併發**：雙擊、重送、兩個 clerk 同時操作同一資源，只能一成一敗或冪等回同結果。
- **冪等**：寫入端點是否需要 Idempotency-Key；同 key 同 payload 回原結果，不同 payload 回 409。
- **跨店隔離**：所有查詢與 FK 參照是否用 `store_id` 範圍；不可用他店 id 湊資料。
- **金額**：全程 `Decimal`，整數元；無 float；rounding policy 明確且測邊界。
- **現金**：任何現金進出都要求 OPEN cash session；購物金不得混入 cash expected。
- **庫存**：不可負庫存；serialized 狀態機只能合法轉移；bulk/catalog 扣量須原子。
- **稽核**：作廢、付款、盤點確認、人工調整、敏感設定是否寫 audit。
- **錯誤碼**：可預期競態回 409/422，不冒 500。
- **匯出**：CSV/XLSX 與 JSON 同源，且防公式注入。

### 4.2 寄售付款閉環

目標端點：

```text
GET  /consignment/settlements
GET  /consignment/payables
POST /consignment/settlements/{id}/pay
```

必問：

- 無 OPEN cash session 是否 409，且 settlement 不變。
- pay 是否鎖 settlement row；兩個 pay 同時送，只能一筆成功。
- payment 成功是否同交易內：settlement `PENDING -> PAID`、cash movement `CONSIGNMENT_PAYOUT_OUT`、audit。
- cash expected 是否扣 payout_amount，且不扣 commission。
- sale void / return 若 settlement 已 PAID，是否不靜默刪帳；需進 reclaim 類流程或明確暫不支援。
- 跨店 settlement id 是否 404/403，不可付到他店。

最小併發測試：

- `asyncio.gather(pay(id), pay(id))`：一個 200/201，一個 409；cash movement count = 1；settlement PAID。

### 4.3 採購 / 補貨

目標：

- suppliers、purchase orders、receive。
- 只處理店內補貨與 catalog quantity 商品；不碰發票、不碰收購作廢。

必問：

- receive 是否 idempotent 或防雙收；同一 PO line 不可重複入庫。
- PO 狀態是否防非法轉移：DRAFT / ORDERED / RECEIVED / CLOSED。
- 收貨是否同交易內更新 catalog quantity + stock_movement IN + receipt record。
- 部分收貨與超收政策是否明確；第一版若不做部分收貨，要用 schema/service 擋下。
- supplier / PO / catalog_product 是否全屬同店。
- unit_cost 若進報表成本，是否 Decimal 整數元，且不追溯改歷史 COGS。

最小併發測試：

- 同一 receive request 並發兩次：不可 quantity 加兩次；stock_movement count 正確。

### 4.4 盤點 / 庫存調整

目標：

- 建盤點單、輸入實點、預覽差異、確認調整。

必問：

- stocktake 建立時是否記錄 system_qty snapshot；確認時是否重讀最新庫存並處理競態。
- close/confirm 是否只能成功一次。
- serialized：實點 0/1 對應狀態或調整語意必須明確；不可把 SOLD 品調回 IN_STOCK 除非流程允許。
- catalog/bulk：調整後不可負；bulk remaining_qty 不可超 total_qty，除非同時有明確 total 調整流程。
- stock_movement ADJUST 是否 append-only，ref 指向 stocktake。
- audit 是否記錄確認者與差異摘要。
- 與銷售/收購並發時 lock order 是否一致，避免庫存列與 movement 寫入 deadlock。

最小併發測試：

- sale 扣同一 catalog/bulk 與 stocktake confirm 同時發生：結果不能負庫存；其中一方需乾淨 409 或重算後成功。

### 4.5 報表任務

必問：

- 報表是否 read-only；測試可檢查 DB row count 不變。
- 所有日期區間是否 `[from, to)`，避免日界重複計入。
- void / reversal / cancelled / paid 狀態是否有明確納入或排除規則。
- JSON 與 export 是否同源，避免匯出另寫一套查詢。
- 報表欄位 N/A 是否顯示為 null / "N/A" 一致；不可用 0 假裝未知成本。

## 5. Review 提示詞模板

```text
請做 Codex adversarial review，base=main。

任務：
- [填任務名，例如 consignment payout pay endpoint]

請優先找：
- transaction rollback 後是否留下半套副作用
- lock order / deadlock / concurrent double-submit
- idempotency key 或重複提交問題
- cross-store reference
- Decimal / rounding / cash expected / stock movement 不一致
- audit 缺漏
- 可預期競態是否冒 500

請用 findings-first 格式，列 file:line、severity、可重現情境、建議修法。
```
