# 金流程式碼稽核（2026-08）

唯讀稽核。本文件是本次稽核的唯一輸出，稽核期間不修改任何實作或測試。

- 稽核基準：`main` @ `93cd4b7`
- 範圍：收購/寄售建單與定價、購物金帳本、寄售結算抽成、錢櫃現金異動、稅額與四捨五入、
  收據/發票金額來源，以及以上的 DB transaction 邊界與併發控制。
- 範圍外：前端樣式/文案、硬體代理印表機協定細節、認證授權、作廢/更正流程設計。

---

## 階段 1：金額相關程式碼盤點

「DB 寫入」＝該檔本身會 `session.add` / UPDATE / DELETE / `commit`；
「外部副作用」＝網路呼叫、SSE 推送、觸發列印等 DB 以外的作用。

### 1.1 共用金額核心

| 模組 | 檔案 | 職責 | DB 寫入 | 外部副作用 |
|---|---|---|---|---|
| core | `backend/app/core/money.py` | `round_ntd` / `discounted_price` / `suggested_price` / `split_tax_inclusive` / `commission`；全系統唯一的四捨五入與稅拆分實作 | 否 | 否 |
| core | `backend/app/core/audit.py` | 稽核紀錄寫入（金額異動留痕） | 是 | 否 |
| frontend | `frontend/lib/money.ts` | `parseNtd` / `formatNtd`：整數元解析與顯示 | 否 | 否 |

### 1.2 銷售 / POS

| 模組 | 檔案 | 職責 | DB 寫入 | 外部副作用 |
|---|---|---|---|---|
| sales | `backend/app/modules/sales/models.py` | `sales`(subtotal/tax/total)、`sale_lines`(unit_price/line_total/discount_amount/manual_discount_amount/net_amount/cost_snapshot)、`sale_tenders`(amount/fee_amount)、購物金套用(requested_value/applied_amount)、`linepay_*`(amount/refunded_amount) | 是（ORM 定義） | 否 |
| sales | `backend/app/modules/sales/service.py` | 結帳主流程：報價、活動折扣、手動折扣、贈品、稅拆、tender 分配、購物金扣抵、LINE Pay 收/退、作廢、毛利計算、冪等重放 | 是 | 是（LINE Pay HTTP） |
| sales | `backend/app/modules/sales/pricing.py` | 折扣計算與**最大餘額法**分攤（`apply_discounts`、`_allocate_largest_remainder`） | 否 | 否 |
| sales | `backend/app/modules/sales/repository.py` | 銷售/明細/tender 讀寫、毛利彙總（`period_margin`、`margin_breakdown`） | 是 | 否 |
| sales | `backend/app/modules/sales/router.py` | `POST /sales`、`POST /sales/quote`、`POST /sales/{id}/void`、`POST /sales/{id}/print-detail`、LINE Pay 退款待處理/解決 | 是（commit） | 否 |
| sales | `backend/app/modules/sales/linepay.py` | LINE Pay Offline 收款/退款 client | 否 | 是（HTTP） |
| sales | `backend/app/modules/sales/inputs.py` / `schemas.py` | 邊界金額型別與驗證 | 否 | 否 |
| sales | `backend/app/modules/sales/reasons*.py` | 贈品/折扣原因主檔 | 是 | 否 |
| campaigns | `backend/app/modules/campaigns/service.py`、`router.py` | 限時活動折扣率與生效區間（結帳時取用） | 是 | 否 |

### 1.3 收購（買斷 / 散裝）與定價

| 模組 | 檔案 | 職責 | DB 寫入 | 外部副作用 |
|---|---|---|---|---|
| acquisition | `backend/app/modules/acquisition/models.py` | `acquisitions.total_cash_paid`、`payout_cash_amount`、`payout_credit_cash_equivalent` | 是（ORM 定義） | 否 |
| acquisition | `backend/app/modules/acquisition/service.py` | 建單：收購總額計算、現金/購物金拆分（`_split_payout`）、序號品與散裝堆建立、錢櫃現金支出、購物金撥入、切結書綁定、作廢反轉、冪等重放 | 是 | 否 |
| acquisition | `backend/app/modules/acquisition/router.py` | `POST /acquisitions`、`GET /{id}`、`GET /{id}/receipt`、`POST /{id}/void` | 是（commit） | 否 |
| inventory | `backend/app/modules/inventory/models.py` | `serialized_items`(acquisition_cost/listed_price)、`bulk_lots`(acquisition_cost/unit_price/remaining_qty)、`catalog_products`(unit_price/unit_cost)、`category_pricing_rules`(min_price_multiple) | 是（ORM 定義） | 否 |
| inventory | `backend/app/modules/inventory/service.py`、`repository.py`、`router.py` | 售價維護（序號品/商品/散裝堆改價）、庫存狀態機、散裝扣量、定價規則 | 是 | 否 |
| inventory | `backend/app/modules/inventory/pricing_defaults.py` | 分類定價規則 seed 常數（折扣上限/最低毛利/最低倍數） | 否 | 否 |
| menu | `backend/app/modules/menu/models.py`、`service.py`、`router.py` | 餐飲品項 `unit_price` 維護 | 是 | 否 |

### 1.4 寄售

| 模組 | 檔案 | 職責 | DB 寫入 | 外部副作用 |
|---|---|---|---|---|
| consignment | `backend/app/modules/consignment/models.py` | `consignment_settlements`(gross / commission_amount / payout_amount / 狀態 / reclaim_needed) | 是（ORM 定義） | 否 |
| consignment | `backend/app/modules/consignment/service.py` | 售出時建結算單、抽成計算、`pay_settlement` 付款（含錢櫃現金流出、冪等鍵）、退貨/作廢時反轉結算 | 是 | 否 |
| consignment | `backend/app/modules/consignment/repository.py`、`router.py` | 結算清單、`POST /consignment/settlements/{id}/pay` | 是 | 否 |

### 1.5 購物金帳本

| 模組 | 檔案 | 職責 | DB 寫入 | 外部副作用 |
|---|---|---|---|---|
| storecredit | `backend/app/modules/storecredit/models.py` | `store_credit_ledger`(signed_amount / balance_after / cash_equivalent / premium_rate_applied)、`store_credit_accounts.balance`、建議率紀錄 | 是（ORM 定義） | 否 |
| storecredit | `backend/app/modules/storecredit/service.py` | `credit` / `debit` / `refund_for_sale_return` / `reverse*` / `adjust` / `get_balance(_for_update)` / `reconcile`；帳本唯一寫入點（`_write_entry`） | 是 | 否 |
| storecredit | `backend/app/modules/storecredit/repository.py` | 帳戶列鎖（`lock_account`）、分錄插入、餘額 SUM、鏈結驗證（`rows_violating_chain`） | 是 | 否 |
| storecredit | `backend/app/modules/storecredit/engine.py` | 加碼率建議引擎（純函式，含 `round_half_pp`） | 否 | 否 |
| storecredit | `backend/app/modules/storecredit/metrics.py`、`suggestion_service.py` | 加碼率指標與建議紀錄 | 是（suggestion_service） | 否 |
| storecredit | `backend/app/modules/storecredit/router.py` | `GET /contacts/{id}/store-credit`、`POST .../adjustments` | 是（commit） | 否 |

### 1.6 錢櫃現金

| 模組 | 檔案 | 職責 | DB 寫入 | 外部副作用 |
|---|---|---|---|---|
| cashdrawer | `backend/app/modules/cashdrawer/models.py` | `cash_sessions`(opening_float/counted_amount/expected_amount/variance)、`cash_movements.amount` | 是（ORM 定義） | 否 |
| cashdrawer | `backend/app/modules/cashdrawer/service.py` | 開帳、記錄異動（`record_movement`）、應有現金推算（`expected_amount`）、分項彙總、結帳對帳 | 是 | 否 |
| cashdrawer | `backend/app/modules/cashdrawer/router.py` | `POST /cash-sessions/open`、`/{id}/movements`、`/{id}/close` | 是（commit） | 否 |

### 1.7 退貨 / 退款

| 模組 | 檔案 | 職責 | DB 寫入 | 外部副作用 |
|---|---|---|---|---|
| returns | `backend/app/modules/returns/models.py` | `customer_returns.refund_amount`、明細 refund_amount、退款分項 amount | 是（ORM 定義） | 否 |
| returns | `backend/app/modules/returns/service.py` | 退貨預覽/建立、退款分配、庫存回復、購物金沖回、錢櫃退現、寄售結算反轉、發票處置決策與折讓 | 是 | 否 |
| returns | `backend/app/modules/returns/refund.py` | 差額法退款計算（`refund_entitlement`、`line_refund_amount`），純函式 | 否 | 否 |
| returns | `backend/app/modules/returns/invoice_policy.py` | 退貨時作廢 vs 折讓的純決策邏輯 | 否 | 否 |
| returns | `backend/app/modules/returns/router.py` | `POST /returns`、`POST /returns/preview`、`GET /returns/{id}` | 是（commit） | 否 |

### 1.8 電子發票 / 稅

| 模組 | 檔案 | 職責 | DB 寫入 | 外部副作用 |
|---|---|---|---|---|
| einvoice | `backend/app/modules/einvoice/models.py` | `invoices`(net/tax/total/tax_rate)、折讓(net/tax/total)、上傳佇列 | 是（ORM 定義） | 否 |
| einvoice | `backend/app/modules/einvoice/service.py` | 開立/手開登記/作廢/折讓、佇列送出與結果回寫、補印 payload、證明聯列印標記 | 是 | 是（經 amego client） |
| einvoice | `backend/app/modules/einvoice/amego.py` | Amego API client（開立/作廢/折讓/查詢） | 否 | 是（HTTP） |
| einvoice | `backend/app/modules/einvoice/serializer.py`、`dropper.py`、`repository.py`、`router.py` | payload 組裝、佇列丟棄、佇列端點 | 是（repository/router/dropper） | 否 |
| settings | `backend/app/modules/settings/models.py`、`service.py`、`router.py` | `tax_rate`、`premium_rate(_min/_max)`、`store_credit_min_spend`、`linepay_fee_pct`、`taiwanpay_fee_pct`、發票開關等 | 是 | 否 |

### 1.9 採購（進項）

| 模組 | 檔案 | 職責 | DB 寫入 | 外部副作用 |
|---|---|---|---|---|
| purchasing | `backend/app/modules/purchasing/models.py` | `purchase_order_lines.unit_cost`、進項發票 `invoice_total/net/tax` | 是（ORM 定義） | 否 |
| purchasing | `backend/app/modules/purchasing/service.py`、`repository.py`、`router.py` | 採購單建立/送出/取消、收貨入庫成本、進項發票登記（含稅拆分） | 是 | 否 |
| stocktake | `backend/app/modules/stocktake/*` | 盤點（數量，不直接記金額；影響庫存價值報表） | 是 | 否 |

### 1.10 報表（讀取金額，不寫入）

| 模組 | 檔案 | 職責 | DB 寫入 | 外部副作用 |
|---|---|---|---|---|
| reports | `backend/app/modules/reports/finance_router.py` | 每日現金/摘要、趨勢、洞察、庫存價值、寄售應付、銷售毛利、折扣、內用、贈品、活動成效 | 否 | 否 |
| reports | `backend/app/modules/reports/router.py` | 購物金負債/流量/成效/對帳 | 否 | 否 |
| reports | `backend/app/modules/reports/service.py`、`aging.py`、`export.py` | 報表彙總、帳齡 FIFO 分桶、CSV/Excel 匯出 | 否 | 是（產生檔案回應） |

### 1.11 金額的呈現與列印來源

| 模組 | 檔案 | 職責 | DB 寫入 | 外部副作用 |
|---|---|---|---|---|
| customerdisplay | `backend/app/modules/customerdisplay/models.py` | `cart_sessions.snapshot`(JSONB，含客顯金額)、`cart_session_events`（DB trigger 強制 insert-only） | 是（ORM 定義） | 否 |
| customerdisplay | `backend/app/modules/customerdisplay/service.py`、`repository.py`、`router.py` | 購物車快照與版本推進、kiosk 配對、SSE 推送 | 是 | 是（SSE） |
| signing | `backend/app/modules/signing/models.py`、`service.py`、`router.py` | 簽署任務 `content` JSONB（含收購/購物金金額快照）、內容雜湊與消耗 | 是 | 否 |
| frontend | `frontend/lib/agent.ts` | 送明細聯 / 發票證明聯 / 收購憑證聯 / 標籤 / 開錢櫃到硬體代理 | 否 | 是（HTTP 至代理、實體列印） |
| hardware-agent | `hardware-agent/agent/drivers/escpos_receipt.py` | ESC/POS 版面排版（只排版、不做金額運算） | 否 | 是（列印） |
| frontend | `frontend/features/*`（pos / acquisition / returns / reports / settings…） | 畫面金額組裝與顯示 | 否 | 否 |

### 1.12 資料庫金額欄位型別（既有事實）

- 幾乎所有金額欄位為 `NUMERIC(12, 0)`（整數元）。
- 費率欄位為 `NUMERIC(5, 4)`：`settings.tax_rate`、`premium_rate(_min/_max)`、`linepay_fee_pct`、
  `taiwanpay_fee_pct`、`invoices.tax_rate`、`store_credit_ledger.premium_rate_applied`。
- `inventory.category_pricing_rules.min_price_multiple` 為 `NUMERIC(5, 2)`（倍數，非金額）。
- **例外**：`sales/models.py:280` `SaleStoreCreditApplication.requested_value` 為 `NUMERIC(12, 2)`，
  與同表 `applied_amount`（`NUMERIC(12, 0)`）及全系統整數元慣例不一致 → 列入階段 2 查證。

---

---

## 階段 2：逐條查證

證據一律引「檔案:行號」＋原始碼片段。判定用「符合 / 不符合 / 待確認」。
本節只描述現況、風險與成因，不提修法。

---

# P0：會產生錯帳或掉錢

## P0-1　購物金銷售「作廢」在真實 COMMIT 下會被資料庫守衛擋掉

**判定：不符合。**

DB 層有一道 deferred constraint trigger，規定「SALE_VOID 沖正只能對應已作廢的銷售」，
而它判定「已作廢」用的是 **`sales.invoice_status = 'VOID'`**：

`backend/app/modules/sales/models.py:551-566`
```sql
CREATE OR REPLACE FUNCTION sales_ledger_sale_debit_guard() RETURNS trigger AS $$
DECLARE
  sale_status TEXT;
BEGIN
  IF NEW.entry_type = 'REVERSAL' AND NEW.source_type = 'SALE_VOID' THEN
    SELECT invoice_status INTO sale_status
      FROM sales WHERE id = NEW.source_id AND store_id = NEW.store_id;
    IF NOT FOUND OR sale_status <> 'VOID' THEN
      RAISE EXCEPTION 'SALE_VOID 沖正只能對應已作廢的同店銷售';
    END IF;
```
掛法（`backend/app/modules/sales/models.py:589-591`）：
```sql
CREATE CONSTRAINT TRIGGER trg_ledger_sale_debit_backing
AFTER INSERT OR UPDATE ON store_credit_ledger
DEFERRABLE INITIALLY DEFERRED
```

但 2026-08-01 的重構已把「銷售是否作廢」搬到 `sales.status`，並明文禁止再用 invoice_status 代替：

`backend/app/shared/enums.py:161-163`
```
**VOIDED 是「這筆銷售是否有效」的唯一事實來源**：報表、清單與後續操作一律以
`sale.status != VOIDED` 判斷，不得再用 `invoice_status == VOID` 代替——後者是
**發票**的狀態，電子發票關閉時根本沒有發票，兩者語意不同（見 ADR）。
```

`void_sale()` 現在只設 `sale.status`，`invoice_status` 交由發票實況決定
（`backend/app/modules/sales/service.py:2054-2101`）：
```python
        before = sale.status.value
        sale.status = SaleStatus.VOIDED
        ...
        await self._storecredit.reverse_for_sale_void(       # ← 插入 REVERSAL/SALE_VOID
            sale.store_id, sale.id, created_by=actor_user_id
        )
        ...
        if voided_invoice is not None:
            if voided_invoice.status is not InvoiceStatus.VOID:
                sale.invoice_status = SaleInvoiceStatus.PENDING_VOID
            elif voided_invoice.issue_channel is EInvoiceIssueChannel.MANUAL_PAPER:
                sale.invoice_status = SaleInvoiceStatus.VOID
            else:
                sale.invoice_status = SaleInvoiceStatus.NOT_ISSUED
```
`void_invoice_for_sale` 在沒有發票時直接 no-op（`backend/app/modules/einvoice/service.py:456-458`）：
```python
        invoice = await self._repo.find_invoice_by_sale(store_id, sale_id)
        if invoice is None:
            return None
```

**後果**：電子發票關閉（或發票尚未核可）時，作廢一筆用購物金付款的銷售，
交易結束時 `invoice_status` 仍是 `NOT_ISSUED`，deferred trigger 於 COMMIT 觸發並
`RAISE EXCEPTION` → 整筆作廢失敗。`invoice_status` 會等於 `'VOID'` 的只剩手開紙本一條路
（`service.py:2099`）與 F0501 平台核可回呼（`service.py:2192`）。

**為什麼測試沒抓到**：測試把每個案例包在外層交易裡、session 以 savepoint 加入，
應用層的 `commit()` 只是釋放 savepoint，**不是真正的 COMMIT**，因此
DEFERRABLE INITIALLY DEFERRED 的 constraint trigger 在一般 pytest 路徑永遠不會觸發。

`backend/tests/conftest.py:4-8`
```
- 每個測試在獨立的外層交易中執行，結束時 rollback，資料不落地、測試間不互相污染。
- session 以 join_transaction_mode="create_savepoint" 加入外層交易，
  因此即使測試內呼叫 commit()，也只是釋放 savepoint，外層 rollback 仍會整批丟棄。
```
既有的 `backend/tests/integration/test_sales_tenders.py:389` 「作廢購物金銷售」用的正是這種
session，所以綠燈；而同檔 `:794` 那個**真的開獨立 session、真的 commit** 的測試
（`test_sale_void_reversal_requires_voided_sale`）反而是在斷言這道守衛「有效」，
其註解 `# 銷售仍 NOT_ISSUED（未作廢）→ 直插 SALE_VOID 沖正應被擋`（`:822`）
正是重構前的舊語意。

**成因**：拆分生命週期的 commit `1999361`（2026-08-01）沒有動到 `backend/app/modules/sales/models.py`
（`git show --stat 1999361` 檔案清單裡沒有它），也沒有任何 migration 更新這兩個函式
（全 72 支 migration 中只有 `f1a2b3c4d5e6_add_sale_tenders_and_payment_methods.py:81` 安裝過它們）。

**待確認（線上確證）**：本稽核為唯讀、未對資料庫執行任何操作。要坐實成「線上已壞」需其一：
(a) 對真資料庫執行一次「購物金付款 → 作廢」並觀察 COMMIT 是否 RAISE；
(b) 查 `pg_get_functiondef('sales_ledger_sale_debit_guard'::regproc)` 確認線上函式體
與 `models.py` 目前定義一致（見 P2-2：這兩者有可能不一致）。

## P0-2　同月整筆退貨（購物金付款）在發票作廢回執落地時會被同一組守衛擋掉

**判定：不符合。**（與 P0-1 同根因、方向相反）

收款側守衛要求「`invoice_status='VOID'` 且有購物金收款 ⟹ 必須存在 SALE_VOID 沖正」：

`backend/app/modules/sales/models.py:462-489`
```sql
  SELECT store_id, buyer_contact_id, invoice_status
    INTO sale_store, sale_buyer, sale_status
    FROM sales WHERE id = p_sale_id;
  ...
  -- 已作廢且有購物金扣抵 → 必須有對應沖正（第三輪 P2：raw UPDATE 設 VOID 不可漏沖回）
  IF sc_tender > 0 AND sale_status = 'VOID' THEN
    PERFORM 1 FROM store_credit_ledger
     WHERE store_id = sale_store AND source_type = 'SALE_VOID' AND entry_type = 'REVERSAL'
       AND source_id = p_sale_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION '已作廢的購物金銷售必須有對應的沖正分錄（SALE_VOID）';
    END IF;
  END IF;
```
觸發器掛在 `sales` 上（`backend/app/modules/sales/models.py:529-532`）：
```sql
CREATE CONSTRAINT TRIGGER trg_sales_tender_total
AFTER INSERT OR UPDATE ON sales
DEFERRABLE INITIALLY DEFERRED
```

但「同月整筆退貨 ⟹ 作廢原發票」的路徑（ADR-014）並**不會**產生 SALE_VOID 沖正——
退貨回補購物金走的是 REFUND/SALE_RETURN：

`backend/app/modules/returns/service.py:595-603`
```python
        if store_credit_refund > 0:
            ...
            await StoreCreditService(self._session).refund_for_sale_return(
                store_id,
                sale.buyer_contact_id,
                amount=store_credit_refund,
                return_id=customer_return.id,
                created_by=actor_user_id,
            )
```
而該路徑最終會把 `invoice_status` 推到 `VOID`
（`backend/app/modules/sales/service.py:2181-2192`，由 F0501 核可回呼呼叫）：
```python
    async def mark_invoice_voided(self, store_id: int, sale_id: int) -> None:
        """平台**確認**作廢（F0501 核可）後才把 invoice_status 轉 VOID。
        ...
            sale.invoice_status = SaleInvoiceStatus.VOID
```

**後果**：購物金付款的銷售在同月整筆退貨後，F0501 平台核可的回執寫回
（`UPDATE sales SET invoice_status='VOID'`）會在 COMMIT 時 RAISE，回執處理永遠失敗，
發票狀態卡在 `PENDING_VOID`。同 P0-1，pytest 的 savepoint 環境抓不到。

---

# P1：特定條件下數字不一致

## P1-1　散裝每件成本除不盡時，COGS 逐筆被資料庫默默四捨五入，加總 ≠ 收購成本

**判定：不符合。**

每件成本是精確除法、**不取整**：

`backend/app/modules/inventory/service.py:1127-1130`
```python
    def per_piece_cost(lot: BulkLot) -> Decimal:
        """每件成本 = acquisition_cost / total_qty。"""
        return lot.acquisition_cost / Decimal(lot.total_qty)
```
成交時直接乘上數量寫進 `cost_snapshot`，中間沒有任何 `round_ntd`：

`backend/app/modules/sales/service.py:3024-3031`
```python
                **self._line_amounts(
                    disc,
                    qty=line.qty,
                    cost=InventoryService.per_piece_cost(lot) * line.qty,
                    gift=gift,
                ),
```
而該欄是 `Numeric(12, 0)`（`backend/app/modules/sales/models.py:186`）：
```python
    cost_snapshot: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
```
→ 取整發生在 PostgreSQL 的型別轉換，不是在 `core/money.round_ntd()`，
與 CLAUDE.md §6「邊界以 ROUND_HALF_UP quantize 到整數元」的規定路徑不同。

**實際偏差**（本機以相同算式驗證）：
```
per piece 333.3333333333333333333333333  單件落庫 333  ×3 = 999   （收購成本 1000）
```
報表的自有散裝 COGS 直接加總 `cost_snapshot`：

`backend/app/modules/sales/repository.py:774-778`
```python
            owned_bulk_revenue += net_amount
            if cost_snapshot is not None:
                owned_bulk_cogs += cost_snapshot
            elif total_qty and total_qty > 0:
                owned_bulk_cogs += round_ntd(acquisition_cost * Decimal(qty) / Decimal(total_qty))
```
→ 整堆賣完後，認列成本比實際收購成本少（或多）數元，毛利同額偏差。

**測試涵蓋**：既有測試只用整除的例子（`backend/tests/test_inventory.py:149-153`，
1000 ÷ 8 = 125），除不盡的情形沒有任何測試。

## P1-2　同一批散裝有兩套 COGS 口徑（四捨五入 vs 精確值）

**判定：不符合。**

`margin_breakdown` 用落庫的整數 `cost_snapshot`（見上），
`goods_margin_and_revenue` 卻用**未取整**的精確除法：

`backend/app/modules/sales/repository.py:531-535`
```python
        for acquisition_cost, total_qty, qty, net_amount in bulk:
            goods_revenue += net_amount
            if total_qty and total_qty > 0:
                cost = acquisition_cost * Decimal(qty) / Decimal(total_qty)
                buyout_margin += net_amount - cost
```
兩者對同一批交易會算出不同毛利。目前 `goods_margin_and_revenue` 只餵給溢價建議引擎
（`backend/app/modules/sales/service.py:1824`，唯一呼叫點），且該處已自陳另一項口徑差異：

`backend/app/modules/sales/service.py:1820-1823`
```python
        # 已知限制（裁示 2026-07-16「其餘文件化」）：此處**不套**退貨扣減。period_margin
        # 僅供 SC-5b 溢價建議引擎（分析用、非帳務），D-8 退貨扣減已在 margin_breakdown
        # （R2/R5/R6/C4 主帳務口徑）落實；影響量在模擬中為全期營收 0.05%。
```
但「四捨五入 vs 精確值」這一項並未被記錄為已知限制。

## P1-3　分次折讓會讓沖回的銷項稅與原發票稅額對不平

**判定：不符合。**

發票與折讓各自在**自己的總額層級**拆稅，兩者互不相干：

`backend/app/modules/einvoice/service.py:601-609`
```python
        prior = await self._repo.sum_allowances_total(store_id, invoice_id)
        if prior + total > invoice.total:
            raise AllowanceExceedsInvoice(
                f"折讓累計 {prior + total} 超過原發票總額 {invoice.total}"
            )

        net, tax = split_tax_inclusive(total, Decimal(invoice.tax_rate))
```
守衛只看 **total 累計**，沒有守 `Σ allowance.net ≤ invoice.net` 或
「全額折讓時 Σ tax 必須等於 invoice.tax」。

**實際偏差**（以 `core/money.split_tax_inclusive` 的算式驗證）：
```
invoice 100 -> (net 95, tax 5)
allowance 50 -> (net 48, tax 2)
兩張 50 元折讓合計 -> net 96, tax 4
```
整張 100 元發票分兩次各退 50 元，全部折讓完之後，沖回的銷項稅只有 4 元，
原發票課的是 5 元 → 稅額殘留 1 元。一次退 100 元則無此問題（net 95 / tax 5）。
這正是稽核範圍中問的「逐項加總與總額課稅是否會產生 1 元差」，答案是**會**，
而且發生在折讓維度。

## P1-4　進項發票的稅額是用本店稅率推算，不是憑證上的實際稅額

**判定：不符合（且為刻意設計，風險未被記錄）。**

`backend/app/modules/purchasing/schemas.py:145-161`
```python
class InputInvoiceIn(BaseModel):
    """進項發票登錄輸入（裁示 2026-07-11：收貨時選填、漏登可補登一次）。

    號碼＝2 英文大寫＋8 數字；金額為含稅整數元字串（>0）。未稅/稅額由後端以
    settings.tax_rate 用 split_tax_inclusive 拆分（§6），不收前端算的值。
    """

    invoice_number: str = Field(pattern=r"^[A-Z]{2}[0-9]{8}$")
    invoice_date: date
    invoice_total: NTDAmount
```
`backend/app/modules/purchasing/service.py:299-311`
```python
    def _invoice_fields(
        invoice: "InputInvoiceIn", tax_rate: Decimal
    ) -> dict[str, object]:
        """進項發票欄位＋稅額拆分（§6：net = round_ntd(total/(1+rate))、tax = total − net）。"""
        net, tax = split_tax_inclusive(Decimal(invoice.invoice_total), tax_rate)
```
供應商開的若是免稅、零稅率，或其系統的進位方式不同，`invoice_net` / `invoice_tax`
就與手上那張憑證不符，且畫面上無從察覺（使用者只輸入總額）。

## P1-5　手動現金調整沒有冪等保護，重送會產生第二筆調整

**判定：不符合。**

`backend/app/modules/cashdrawer/router.py:81-119`
```python
@router.post(
    "/{session_id}/movements",
    response_model=CashMovementRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="recordCashMovement",
)
async def record_cash_movement(
    session_id: int,
    payload: CashMovementCreateRequest,
    session: SessionDep,
    user: ManagerDep,
) -> CashMovementRead:
```
簽章裡沒有 `Idempotency-Key` 標頭，service 也沒有任何去重
（`backend/app/modules/cashdrawer/service.py:75-124` 只鎖 session 後直接 `add_movement`）。
全系統其他影響金錢的端點都有：`sales`(`router.py:222`)、`returns`(`router.py:68`)、
`acquisitions`(`router.py:117`)、`consignment` 付款(`router.py:68`)、
`store-credit` 校正(`router.py:68`)、`purchasing` 收貨(`router.py:333`)。

**後果**：雙擊或網路重試會寫兩筆 `MANUAL_ADJUST`，`expected_amount` 直接被改兩次
（`backend/app/modules/cashdrawer/service.py:142-143`：`total += movement.amount`），
關帳的 variance 隨之失真。且該表沒有任何唯一約束可兜底（見 P2-1）。

## P1-6　發票品項單價在小計除不盡時會送出 20 位以上小數

**判定：不符合。**

`backend/app/modules/einvoice/amego.py:88-99`
```python
        # Amount（實收小計）為權威；折扣行的 UnitPrice 以小計÷數量表示（兩者一致，
        # 避免平台以 Quantity×UnitPrice 驗算時對不上）。
        effective_unit = Decimal(line.net_amount) / Decimal(line.qty)
        items.append(
            {
                "Description": line.description[:_DESCRIPTION_MAX],
                "Quantity": line.qty,
                "UnitPrice": _decimal_str(effective_unit),
                "Amount": _decimal_str(Decimal(line.net_amount)),
```
`_decimal_str` 只做 normalize、不限位數（`backend/app/modules/einvoice/amego.py:55-58`）：
```python
def _decimal_str(value: Decimal) -> str:
    """Decimal → 無指數、無尾零字串（"450"、"52.5"）；金額欄位以字串傳輸。"""
    text = format(value.normalize(), "f")
    return text
```
`Decimal` 預設 28 位有效位數，`net_amount` 除不盡（例：qty 3、整單折扣後實付 200）時
`UnitPrice` 會是 `"66.66666666666666666666666667"`。註解本身承認目的是讓平台
「以 Quantity×UnitPrice 驗算時對不上」不發生，但這個寫法在除不盡時仍然對不上
（66.666…×3 ≠ 200）。此欄位只在「有臨時折扣且 qty > 1 且分攤後除不盡」時才會出現。

**待確認**：Amego 平台對 `UnitPrice` 的小數位上限、以及是否真的驗算
`Quantity × UnitPrice == Amount`。本 repo 內查不到對真平台的實測佐證。

---

# P2：可維護性風險

## P2-1　`cash_movements` 自稱 append-only，但沒有任何 DB 保護

`backend/app/modules/cashdrawer/models.py:59-60`
```python
class CashMovement(Base):
    """現金異動（append-only 帳；無 updated_at）。"""
```
該表沒有不可變 trigger、沒有 `amount` 的正負/整數 CHECK、沒有 `type` 與符號的一致性約束
（`backend/app/modules/cashdrawer/models.py:59-76` 全文只有欄位定義）。
對照 `store_credit_ledger` 有 15 條 CHECK ＋ 5 個 trigger
（`backend/app/modules/storecredit/models.py:107-153, 229-371`）。
應用層目前沒有任何 UPDATE/DELETE `cash_movements` 的路徑（已全庫搜尋確認），
所以現況正確，但這條不變量完全靠自律。

## P2-2　金流相關的 DB trigger 只在「建表 migration」以 import 常數的方式安裝

`backend/alembic/versions/c5d1e8a2b7f4_add_store_credit_ledger.py:16,183-184`
```python
from app.modules.storecredit.models import LEDGER_IMMUTABLE_DDL, LEDGER_IMMUTABLE_DROP_DDL
...
    for ddl in LEDGER_IMMUTABLE_DDL:
        op.execute(ddl)
```
`backend/alembic/versions/f1a2b3c4d5e6_add_sale_tenders_and_payment_methods.py:81`
```python
    for ddl in SALE_TENDER_TOTAL_GUARD_DDL + SALE_LEDGER_BACKING_DDL:
```
migration 執行的是**當下模組常數的內容**，而不是撰寫該 migration 當時的內容。
後果有二：
1. 之後往常數裡新增/修改 trigger，**不會**有 migration 把它套到已存在的資料庫
   （全 72 支 migration 中沒有任何一支重新安裝這些函式）。
2. 因此「程式碼裡的 trigger 定義」與「線上資料庫裡的 trigger 定義」可能不同，
   讀 code 無法斷定線上行為——這也是 P0-1／P0-2 需要線上確證的原因。

## P2-3　DEFERRABLE constraint trigger 在一般測試路徑永不觸發

見 P0-1 的 conftest 引文。所有 `DEFERRABLE INITIALLY DEFERRED` 的守衛
（`trg_sale_tenders_total`、`trg_sales_tender_total`、`trg_ledger_sale_debit_backing`）
只有少數手動另開 sessionmaker、真的 `commit()` 的測試會踩到
（如 `backend/tests/integration/test_sales_tenders.py:794`）。
結果是：這一層 DB 不變量的迴歸保護遠比表面測試數字薄。

## P2-4　試算與成交是兩份平行實作，靠註解維持一致

`backend/app/modules/sales/service.py:2498-2502`
```python
        """單行試算（唯讀）：解析品項、算折後價；不動任何狀態。

        必須與 `_process_line` 得出**完全相同**的金額——客顯購物車快照由此建立，
        結帳時會與實際成交明細逐欄位比對，兩邊不一致就會整筆結帳失敗。
        """
```
`_quote_line`（`:2491`）與 `_process_line`（`:2752`）各自完整重寫了序號/一般/散裝/餐飲
四種品項的折扣與金額邏輯。只有走客顯購物車的結帳才會被指紋比對抓到不一致；
不走客顯的一般結帳沒有這層保護（收款總額對不上會 422，但金額本身無交叉驗證）。

## P2-5　`tax_rate` 缺小數位數驗證，其餘費率都有

`backend/app/modules/settings/schemas.py:70`
```python
    tax_rate: Annotated[Decimal, Field(ge=0, lt=1)] | None = None
```
對照同檔 `:133-147` 的 `premium_rate` / `linepay_fee_pct` / `taiwanpay_fee_pct`：
```python
    @field_validator("premium_rate", "premium_rate_min", "premium_rate_max")
    @classmethod
    def _rate_scale(cls, value: Decimal | None) -> Decimal | None:
        # DB 為 Numeric(5,4)：限四位小數，避免 API/留痕記 5dp 而 DB 落 4dp 不一致（Codex P2）。
        if value is not None and value != value.quantize(Decimal("0.0001")):
            raise ValueError("溢價率最多四位小數")
```
`settings.tax_rate` 同為 `Numeric(5, 4)`（`backend/app/modules/settings/models.py:52-54`），
輸入 5 位小數會被 DB 靜默捨入，同一請求內記憶體中的物件仍保有原值。

## P2-6　前端手續費顯示走 JS 浮點數

`frontend/app/(authed)/pos/page.tsx:577,616`
```tsx
                <Money value={Math.round(plan.taiwanPay * taiwanpayFeePct)} />
                <Money value={Math.round(plan.linePay * linepayFeePct)} />
```
後端落帳用 Decimal（`backend/app/modules/sales/service.py:1253,1257`）：
```python
                fee = Decimal(round_ntd(tender.amount * settings.taiwanpay_fee_pct))
```
`Math.round` 對正數與 ROUND_HALF_UP 一致，但 `plan.linePay * linepayFeePct` 是二進位浮點乘法，
剛好落在 .5 的金額可能與後端差 1 元。影響僅止於畫面（權威值是後端寫進 `fee_amount` 的那筆）。

## P2-7　測試清理以關閉 replication role 的方式繞過帳本不可變 trigger

`backend/tests/integration/test_sales_signing_concurrency.py:339-345`
```python
            await s.execute(text("SET session_replication_role = replica"))
            ...
            await s.execute(
                text("DELETE FROM store_credit_ledger WHERE store_id = :sid"), {"sid": store_id}
            )
```
只發生在測試庫，本身不是缺陷；但它證明「帳本不可變」在資料庫層是可被有權限者
一行指令關掉的（`TRUNCATE` 同樣不觸發 row trigger，見同目錄多支測試）。
這是稽核題目「有無任何 UPDATE / DELETE 路徑（含 migration、測試 fixture）」的完整答案：
**應用程式碼與 migration 皆無；測試 fixture 有，且是刻意繞過 trigger。**

## P2-8　LINE Pay 退款日誌的鎖定查詢未帶 store_id

`backend/app/modules/sales/service.py:1618-1622`
```python
            row = await ledger.scalar(
                select(LinePayRefundAttempt)
                .where(LinePayRefundAttempt.refund_key == refund_key)
                .with_for_update()
            )
```
同函式開頭的既有列檢查有帶 `store_id`（`:1592-1607`），這裡沒有。
目前安全，因為 `refund_key` 全域唯一（`backend/app/modules/sales/models.py:393`）
且所有產生點都帶 `s{store_id}:` 前綴（`:1391`、`:610`）——但這是命名慣例撐著，不是型別或約束撐著。

---

# 各項查證對照表

## A. 數值正確性

| 項目 | 判定 | 證據 |
|---|---|---|
| 金額全程 Decimal／整數，無 float 進入計算路徑 | 符合 | 全庫無 `float(` 用於金額；`grep ": float"` 只命中天數/小時數（`reports/schemas.py:467`、`backup/service.py:41`）。前端金額為整數 number（`frontend/lib/money.ts`），API 邊界一律字串（各 schema 的 `NTDAmount` PlainSerializer） |
| JSON 序列化不出現 float | 符合 | 金額欄一律 `PlainSerializer(lambda d: str(d))`／`format_ntd`（如 `reports/schemas.py:17`、`cashdrawer/schemas.py:15`） |
| DB Numeric precision/scale 與程式端一致 | **部分不符合** | 金額欄一致為 `Numeric(12,0)`；但 P1-1 的散裝成本靠 DB 隱式捨入、P2-5 的 tax_rate 缺 4dp 驗證。`sale_adjustments.requested_value` 為 `Numeric(12,2)`（`sales/models.py:280`）——經查為**刻意**：該欄存的是使用者輸入值（可能是百分比 12.5），實際折扣額落在整數的 `applied_amount`（`:281`），非缺陷 |
| 四捨五入時機（逐項 vs 總額）與方向一致 | 符合（一處例外） | 稅一律只在總額層級算一次（`core/money.py:57-71`、`einvoice/service.py:216`）；折扣分攤用最大餘額法且每行留 1 元（`sales/pricing.py:122-169`）；退款用差額法（`returns/refund.py:25-45`）；點數沖回同樣用差額法（`returns/service.py:640-647`）。例外＝P1-1 的散裝成本 |
| 後端／前端顯示／發票三處一致 | 符合 | 前端應付總額取後端 quote（`frontend/app/(authed)/pos/page.tsx:1546-1548`），未就緒即鎖住結帳鍵；收據明細印 `net_amount`（`hardware-agent/agent/drivers/escpos_receipt.py:110`）；發票品項亦印 `net_amount` 且強制 `Σ = invoice.total`（`einvoice/amego.py:87,100-102`）。硬體代理全程只排版、無任何金額運算 |
| 5% 稅：含稅反推未稅算式 | 符合 | `net = round_ntd(total/(1+rate))`、`tax = total − net`，恆等 `net+tax=total`，並有 DB CHECK `ck_invoices_net_tax_total`（`einvoice/models.py:81`） |
| 逐項加總 vs 總額課稅的 1 元差 | **不符合** | 見 P1-3（折讓維度會差 1 元） |

補充：階段 1 曾記下 `escpos_receipt.py:110` 的 `line.net_amount or line.line_total` 疑似
把 0 元贈品行 fallback 掉。查證後**不成立**：該欄型別是 `str | None`
（`hardware-agent/agent/interfaces.py:65`），贈品行的值是字串 `"0"`（truthy），
fallback 只在舊版呼叫端沒帶（None）時才生效。

## B. 帳本完整性

| 項目 | 判定 | 證據 |
|---|---|---|
| 購物金帳本真的 append-only | 符合 | 應用層只有 `insert_entry`（`storecredit/repository.py:54`）；DB 有 `trg_store_credit_ledger_immutable` 拒絕 UPDATE/DELETE（`storecredit/models.py:229-241`）；migration 無資料修改路徑；唯一的 DELETE 在測試 fixture 且刻意關 trigger（見 P2-7） |
| 餘額是即時 SUM 還是快取 | 兩者都有，且互相校驗 | `accounts.balance` 為快取，由 DB trigger `store_credit_cache_sync` 以帳本的 `balance_after` 覆寫（`storecredit/models.py:351-365`）；`balance_after` 本身由 `store_credit_balance_chain_guard` 驗證等於滾動和（`:313-342`）；寫入前另在鎖內三方比對，不一致即中止（`storecredit/service.py:139-148`） |
| 重算與對帳機制 | 符合 | `StoreCreditService.reconcile()`（`storecredit/service.py:523`）逐帳戶比對 SUM／快取／最新 balance_after 並回報孤兒帳本，經 `GET /reports/store-credit/reconciliation`（`reports/router.py:200`）暴露；只回報不自動修正 |
| 餘額 < 0 的可達路徑 | 未發現 | 三重防線：service 計算後檢查（`storecredit/service.py:152-155`）、DB CHECK `ck_scl_balance_after_nonneg`（`storecredit/models.py:144`）、帳戶 CHECK `ck_sca_balance_nonneg`（`:185`）。收購作廢遇餘額不足會整筆擋下轉人工（`acquisition/service.py:735-739`） |

## C. 原子性與併發

| 項目 | 判定 | 證據 |
|---|---|---|
| 主檔／明細／庫存／購物金／錢櫃在同一交易 | 符合 | service 層只 `flush`、不 `commit`，由 router 單點 commit（`sales/service.py:9-11` 的模組說明；`sales/router.py:392` 唯一 commit）。收購另在 service 邊界包 savepoint（`acquisition/service.py:392-397`） |
| async session 跨 task 共用 | 符合（一處刻意例外） | session 由 `Depends(get_session)` per-request；排程各自開 sessionmaker（`customerdisplay/scheduler.py:20-21`、`backup/scheduler.py:135`）。刻意例外＝LINE Pay 退款日誌用**獨立交易**（`sales/service.py:1610-1638`），目的是讓日誌在主交易回滾後存活 |
| 同一會員／同一商品的鎖策略 | 符合 | 購物金：帳戶列 `FOR UPDATE` 序列化（`storecredit/repository.py:27`、service `:130`）＋ DB trigger 內再鎖一次（`storecredit/models.py:321-323`）。序號品：售前依 id 升冪預鎖（`sales/service.py:966`）＋原子狀態轉移。錢櫃：開帳 session `FOR UPDATE`（`cashdrawer/service.py:96`）。寄售結算：`get_for_update`＋advisory lock（`consignment/service.py:51-52`）。全域鎖序有明文：cash → store_credit（`sales/service.py:1226-1230`）、settlement → cash_session（`consignment/service.py:48`、`returns/service.py:551-553`） |
| 外部副作用在 commit 之後 | 部分符合（結構性限制） | 列印由前端在收到 200（即 commit 後）才呼叫代理（`frontend/lib/agent.ts`）；SSE 是輪詢已提交狀態、每輪 `rollback` 結束唯讀交易（`customerdisplay/router.py:280-296`）；Amego 上送採兩階段「先 commit 認領、再打 API」（`einvoice/service.py:757-768`）。**LINE Pay 收款是唯一在 commit 前呼叫外部 API 的路徑**（`sales/service.py:1259-1265`），無法避免，其緩解是 check-first ＋ 由冪等鍵導出的 orderId ＋ `PAYMENT_UNCERTAIN` 記錄（`sales/router.py:246-256`） |

## D. 冪等性

| 項目 | 判定 | 證據 |
|---|---|---|
| 重複結帳 | 符合 | `Idempotency-Key` 必填（`sales/router.py:222`）＋內容指紋比對（`sales/service.py:774-786`）＋DB 唯一約束 `uq_sales_store_idempotency_key`（`sales/models.py:60`）＋撞約束後回原單（`sales/router.py:288-318`） |
| 前端重試 | 符合 | 同指紋恆得同鍵並持久化到 localStorage＋記憶體後備（`frontend/lib/idempotency.ts` 的 `getOrCreatePersistedIdemKey`）；409 不可丟棄鍵（`canDiscardIdempotencyKey`） |
| SSE 斷線重連 | 符合 | SSE 只送版本號、不帶金額、不寫入（`customerdisplay/router.py:274`） |
| 重複扣款（LINE Pay） | 符合 | orderId 綁 (店, 冪等鍵, 金額)、先 `check` 再 `pay`、平台金額不符即拒（`sales/service.py:1291-1330`）；退款走 append-only 日誌，PENDING 一律 fail-closed 轉人工（`sales/service.py:1572-1655`） |
| 其他金流端點 | 符合 | 收購／退貨／寄售付款／購物金校正／採購收貨皆必帶鍵（見 P1-5 的清單） |
| **手動現金調整** | **不符合** | 見 P1-5 |

## E. 邊界條件

| 項目 | 判定 | 證據 |
|---|---|---|
| 0 元交易 | 符合 | 僅「整單皆贈品」允許 0 元（`sales/service.py:1000-1006`），且不得有收款明細；DB 端另有 deferred 守衛複驗「有明細且全為贈品」（`sales/models.py:428-444`） |
| 全額購物金支付 | 符合 | 不碰現金、不要求開帳（`sales/service.py:876-878`）；須經已凍結客顯購物車＋簽署（`:861-874`）；扣抵後餘額須等於客人所簽（`:1128-1146`） |
| 購物金不足 | 符合 | `InsufficientStoreCredit` 於 `_write_entry` 拋出（`storecredit/service.py:152`），整筆結帳回滾 |
| 負數金額 | 符合 | 收款（`sales/schemas.py:115-122`）、收購（`acquisition/schemas.py:30-37`）、售價（`inventory/schemas.py:91-94`）、現金（`cashdrawer/schemas.py:19-23`，另擋科學記號 `:36-42`）皆驗證非負整數。`MANUAL_ADJUST` 刻意允許負值 |
| 寄售抽成進位後恆等 | **符合** | `commission_amount = round_ntd(gross × pct/100)`、`payout = gross − commission_amount`（`consignment/service.py:230-231`），兩者皆整數且相加恆等於 `gross`，無捨入殘留。`commission()` 另擋 pct 超出 0–100（`core/money.py:75-83`） |

---

# 待確認

1. **P0-1／P0-2 的線上確證**：需對真資料庫（真 COMMIT）跑一次「購物金付款 → 作廢」，
   或查 `pg_get_functiondef` 比對線上函式體。本稽核唯讀、未對任何資料庫執行操作。
2. **線上資料庫是否具備 `models.py` 目前定義的全部 trigger**（P2-2）：
   需查 `pg_trigger` / `pg_proc`。這會直接影響 P0 與 B 節多項結論的適用範圍。
3. **B2C 電子發票證明聯的銷售額／稅額欄口徑**：送 Amego 的 B2C payload 是
   `SalesAmount = 含稅總額、TaxAmount = 0`（`einvoice/amego.py:110-113`，依 `docs/24:49-54`），
   但列印證明聯送的是本地拆分後的 `invoice.net` / `invoice.tax`
   （`frontend/lib/agent.ts:83-85`）。兩者對同一張發票的表述不同。
   需要的資訊：B2C 收銀機發票證明聯上「銷售額／稅額」欄的法定填法，
   以及 Amego 補印版面（平台產生）是怎麼印的——後者才是與平台紀錄一致的那一份。
4. **Amego 對 `UnitPrice` 的小數位限制與驗算規則**（P1-6）：需平台文件或一次真平台實測。
5. **散裝除不盡時的成本尾差歸屬**（P1-1）：這是業務規則問題（尾差要落在最後一件、
   還是允許整批成本與 COGS 差幾元），本檔不臆測。
6. **進項發票遇免稅／零稅率供應商的處理方式**（P1-4）：需店主裁示是否會有這類供應商。

---

## 階段 3：對真實資料庫的唯讀確證

**方法與界線**：本階段只對本機 PostgreSQL（`127.0.0.1:1234`，容器 `lu-camp-db-1`）執行 `SELECT`。
未建立、未修改、未刪除任何物件或資料列，未執行測試、未啟動或重啟任何服務。
查詢腳本放在 session scratchpad，不進 repo。

現存資料庫與狀態（`pg_database` / `alembic_version` / `information_schema.tables`）：

| 資料庫 | public 表數 | alembic_version | 備註 |
|---|---|---|---|
| `lucamp` | **1** | `a9b0c1d2e3f4` | `.env` 的 `DATABASE_URL` 目標，但**只剩 `alembic_version` 一張表** |
| `lucamp_manual` | 59 | `f2a7c4e19b83`（＝head） | 執行中的 backend 實際連的就是這個庫（`pg_stat_activity`：4 conns） |
| `lucamp_sim` | 58 | `b5d7f9a1c3e2` | 180 天模擬 |
| `lucamp_e2e` | 59 | `e1f3a5c7b9d2` | |
| `lucamp_pytest` | **1** | `f5a6b7c8d9e0` | conftest session 結束 `drop_all`，只剩 `alembic_version` |

資料量（`lucamp_manual` / `lucamp_sim`）：sales 18,123 / 5,310；sale_tenders 19,034 / 5,388；
store_credit_ledger 1,703 / 238；cash_movements 22,963 / 6,217。

---

### 3.1　待確認 #1、#2 已結案

**線上 guard 函式體與 `models.py` 一致，確實是 `invoice_status` 版本。**
`pg_proc.prosrc` 於 `lucamp` / `lucamp_manual` / `lucamp_sim` / `lucamp_pytest` **四個庫全部**回傳：

```
sales_ledger_sale_debit_guard:
   | SELECT invoice_status INTO sale_status
   | IF NOT FOUND OR sale_status <> 'VOID' THEN
sales_verify_store_credit_consistency:
   | SELECT store_id, buyer_contact_id, invoice_status
   | IF sc_tender > 0 AND sale_status = 'VOID' THEN
```

**trigger 確實掛著**（`pg_trigger`，以有資料的 `lucamp_manual` 為例，`lucamp_sim` 相同）：

```
sale_tenders         trg_sale_tenders_total                   deferrable=True initdeferred=True
sales                trg_sales_tender_total                   deferrable=True initdeferred=True
store_credit_ledger  trg_ledger_sale_debit_backing            deferrable=True initdeferred=True
store_credit_ledger  trg_ledger_return_refund_backing         deferrable=True initdeferred=True
store_credit_ledger  trg_store_credit_ledger_acq_source_guard deferrable=True initdeferred=True
store_credit_ledger  trg_store_credit_ledger_immutable        deferrable=False
store_credit_ledger  trg_store_credit_reversal_guard          deferrable=False
store_credit_ledger  trg_store_credit_credit_guard            deferrable=False
store_credit_ledger  trg_store_credit_balance_chain_guard     deferrable=False
store_credit_ledger  trg_store_credit_cache_sync              deferrable=False
cart_session_events  trg_cart_session_events_immutable        deferrable=False
```

→ **P0-1 與 P0-2 的程式碼推論在資料庫層獲得證實**：守衛存在、且判定欄位是 `invoice_status`。

同時證實 **P2-1**：`cash_movements` 在所有庫都**沒有任何 trigger**。

### 3.2　P0-1／P0-2：已確證存在，但**尚未被觸發過**

| 查詢 | `lucamp_manual` | `lucamp_sim` | `lucamp_e2e` |
|---|---|---|---|
| `status='VOIDED'` 且有 STORE_CREDIT 收款的銷售 | **0** | **0** | **0** |
| `status='RETURNED'` 且有 STORE_CREDIT 收款的銷售 | **0** | **0** | — |
| 作廢單的 `invoice_status` 分布 | `NOT_ISSUED` 236、`PENDING_VOID` 10 | `NOT_ISSUED` 89 | `NOT_ISSUED` 7 |

**修正階段 2 的嚴重度描述**：這兩條的失敗模式是 **fail-closed**——守衛會讓那次 COMMIT 整筆
RAISE、交易回滾，**不會**寫出錯誤的金額或帳本列。所以它們至今沒有污染任何一筆資料
（作廢單 `invoice_status` 分布也印證：現有 335 筆作廢單全部沒有購物金腿）。

實際危害是「**唯一的反轉手段失效**」：
- P0-1：店員打錯一筆購物金付款的單，作廢會直接失敗且錯誤訊息是資料庫的
  `SALE_VOID 沖正只能對應已作廢的同店銷售`（對店員不可解）。此時能做的只剩人工調整
  現金/購物金去「湊」——那才是真正產生錯帳的地方。
- P0-2：購物金付款的單同月整筆退貨後，F0501 平台核可的回執寫回會永久失敗，
  發票在平台上已作廢、本地卻停在 `PENDING_VOID`——**這一條會直接造成帳實不符**
  （本地發票狀態與平台紀錄不一致），仍屬 P0。

`lucamp_manual` 有 266 張 `VOID`/`VOID_PENDING` 發票、其中 23 張 `void_reason='FULL_RETURN'`，
代表「整筆退貨作廢發票」的流程已經真的在跑；只是還沒有任何一筆同時用到購物金。
兩者交集一旦發生就會踩到。

### 3.3　P1-1（散裝 COGS 尾差）：已在真實資料中量測到

對「已完售的自有散裝批」比對 `Σ cost_snapshot` 與 `bulk_lots.acquisition_cost`：

| 資料庫 | 完售自有散裝批 | Σ COGS ≠ 收購成本 | 除不盡的自有散裝批 |
|---|---|---|---|
| `lucamp_sim` | 40 | **25** | **39 / 40** |
| `lucamp_manual` | 4 | **2** | **324 / 329** |
| `lucamp_e2e` | 4 | 0 | 6 / 14 |

實例（`lucamp_sim`）：
```
lot=29 收購成本=1131 件數=34 ΣCOGS=1132 差=+1
lot=32 收購成本=756  件數=26 ΣCOGS=754  差=-2
lot=7  收購成本=940  件數=41 ΣCOGS=943  差=+3
lot=15 收購成本=1880 件數=31 ΣCOGS=1883 差=+3
```

→ 這不是邊角案例：**98% 以上的散裝批收購成本都不能被件數整除**，
完售後認列成本與實際收購成本相差 −2 ～ +3 元，**兩個方向都會發生**，
每批獨立累積、不會互相抵銷。P1-1 從「推論」升級為「已量測」。

### 3.4　P1-3、P1-6：觸發前提目前為零（latent）

| 前提 | `lucamp_manual` | `lucamp_sim` |
|---|---|---|
| 分次折讓（同一張發票 >1 張折讓單） | **0** 張 | 0 張 |
| 已全額折讓的發票（可比對稅額守恆） | **0** 張 | 0 張 |
| 已開發票、`qty>1` 且 `net_amount % qty ≠ 0` 的品項行 | **0** 行 | 0 行 |

`lucamp_manual` 有 292 張折讓，全部是「一張發票一張折讓」，所以 P1-3 的 1 元稅差還沒出現；
P1-6 的長小數 `UnitPrice` 也還沒送出過。兩者都是**只要出現分次退貨/多件品折扣就會發生**。

### 3.5　「符合」項的實證（非抽樣，全表比對）

以下全部在 `lucamp_manual`（18,123 筆銷售）與 `lucamp_sim`（5,310 筆）**兩庫皆為 0 例外**：

| 不變量 | 結果 |
|---|---|
| `Σ sale_tenders.amount = sales.total` | 0 筆不對平 |
| `sales.subtotal + sales.tax = sales.total` | 0 筆不符 |
| `Σ sale_lines.net_amount = sales.total` | 0 筆不符 |
| 一般行 `net_amount = line_total − manual_discount_amount` | 0 行不符 |
| `invoices.net + tax = total` 且 `net = round(total/(1+tax_rate))` | 0 張不符 |
| 寄售 `gross = commission_amount + payout_amount` | 2,705 + 260 筆全符 |
| 寄售 `commission_amount = round(gross × pct/100)` | 0 筆不符 |
| 購物金帳戶快取 `balance` = 帳本 `Σ signed_amount` | 316 + 138 個帳戶全符 |
| 帳本滾動鏈 `balance_after` = 累計和（window function 重算） | 0 列斷裂 |
| `balance_after < 0` | 0 列 |
| 錢櫃 `expected = 開帳 + Σ(IN) − Σ(OUT) ± 調整`（重算 vs 落庫） | 487 + 200 個已關帳班別，0 個不符 |
| 錢櫃 `variance = counted − expected` | 0 個不符 |

→ B 節（帳本完整性）、E 節（寄售恆等）、以及 §7.4 現金對帳公式，不只程式碼正確，
在兩份數千至兩萬筆的真實資料上也逐列驗過。

### 3.6　階段 2 遺漏、階段 3 補上的兩道帳本守衛

`pg_trigger` 列出了兩個階段 2 沒讀到的守衛，已補讀原始碼，**判定：正確，無 stale 語意**：

- `store_credit_ledger_acq_source_guard`（`backend/app/modules/acquisition/models.py:266-289`）：
  CREDIT/ACQUISITION 分錄必須對應同店同對象、`payout_credit_cash_equivalent` 等值的收購頭。
  沖正（REVERSAL）在函式開頭即 early return，不受影響。
- `return_ledger_refund_guard`（`backend/app/modules/returns/models.py:242-278`）：
  REFUND/SALE_RETURN 分錄的店、會員、金額必須與 `return_tenders` 的購物金退款列一致。

兩者都以 `entry_type`／`source_type` 判定，沒有引用 `invoice_status`，因此不受 2026-08-01
生命週期拆分影響。

### 3.7　新增觀察

**O-1（P2）`.env` 指向的 `lucamp` 是一個只剩 `alembic_version` 的空殼庫。**
`public` schema 只有 1 張表，`alembic_version` 停在 `a9b0c1d2e3f4`（head 是 `f2a7c4e19b83`）。
表被 `drop_all` 掃掉、版本戳沒跟著清。任何人照 `.env` 連上去或對它 `alembic upgrade head`，
都會得到「版本說已升級到一半、但沒有任何表」的狀態——升級會在第一支 migration 就
`relation does not exist`。實際跑的 backend 連的是 `lucamp_manual`（`pg_stat_activity` 確認），
所以目前沒有影響，但 `.env` 與現實不符這件事本身就是踩雷點。

**O-2（P2）五個庫五個不同的 alembic 版本**（見 3.0 的表）。只有 `lucamp_manual` 在 head。
配合 P2-2（trigger 只在建表 migration 安裝），代表「哪個庫有哪些守衛」取決於它是什麼時候建的，
無法從 repo 推斷——本次要靠 `pg_trigger` 實查才能確定，就是這個原因。

**O-3（P2）文件漂移**：`backend/app/modules/sales/repository.py:460` 的 docstring 仍寫
```
作廢單以 invoice_status != VOID 排除（與毛利口徑一致）。
```
但同函式的實際查詢已是 `Sale.status != SaleStatus.VOIDED`（`:471`）。程式碼正確、註解過時。
這是掃描 2026-08-01 重構遺留時的副產品——**應用程式碼層面只找到這一處註解漂移，
沒有其他 `invoice_status` 被當成「銷售是否有效」使用的實作**（全庫 grep 確認），
遺留全部集中在兩支 DB 函式。

---

## 稽核狀態總結（階段 3 後）

| 編號 | 事項 | 程式碼證據 | 真實資料 |
|---|---|---|---|
| P0-1 | 購物金銷售作廢被 DB 守衛擋掉 | 已確證（含線上 `pg_proc`） | 尚未觸發（0 筆） |
| P0-2 | 購物金＋整筆退貨的 F0501 回執寫回失敗 | 已確證（含線上 `pg_proc`） | 尚未觸發（0 筆） |
| P1-1 | 散裝 COGS 逐件捨入、加總 ≠ 收購成本 | 已確證 | **已發生**（sim 25/40 批，−2～+3 元） |
| P1-2 | 兩套散裝 COGS 口徑 | 已確證 | 影響僅溢價建議引擎 |
| P1-3 | 分次折讓稅額不守恆 | 已確證（算式驗證） | 尚未觸發（0 張分次折讓） |
| P1-4 | 進項稅額以本店稅率推算 | 已確證（刻意設計） | 需業務裁示 |
| P1-5 | 手動現金調整無冪等鍵 | 已確證 | 無法從資料回溯判斷是否發生過 |
| P1-6 | 發票 UnitPrice 長小數 | 已確證 | 尚未觸發（0 行） |
| P2-1 | `cash_movements` 無 DB 保護 | 已確證 | 線上 `pg_trigger` 確認無 trigger |
| P2-2 | trigger 只在建表 migration 安裝 | 已確證 | O-2 印證（五庫五版本） |
| P2-3~P2-8 | 見階段 2 | 已確證 | — |
| O-1~O-3 | 階段 3 新增 | 已確證 | — |

**仍未解的待確認**（需要 repo 以外的資訊，本稽核無法自行判定）：

1. **B2C 電子發票證明聯的銷售額／稅額欄法定填法**——送平台的是
   `SalesAmount=含稅總額、TaxAmount=0`，印在紙上的是本地拆分的 `net`/`tax`，兩者不同。
   需要：法規口徑，或 Amego 補印版面（平台產生那一份）的實際內容。
2. **Amego 對 `UnitPrice` 的小數位限制、是否驗算 `Quantity × UnitPrice = Amount`**（P1-6）。
3. **散裝除不盡時的成本尾差要落在哪**（P1-1）——業務規則問題，不臆測。
4. **進項發票是否會遇到免稅／零稅率供應商**（P1-4）——需店主裁示。

---

## 階段 4：收尾（四個先前未掃到的角落）

### 4.1　報價後改價：POS 是安全的，但 API 合約留了一條無金額確認的路

**判定：符合（POS 路徑）／P2（API 合約）。**

改價端點沒有任何「與進行中結帳」的互斥
（`backend/app/modules/inventory/router.py:90-110,130-146,148-169`，三個 PATCH 都是改完就 commit）。
`/sales/quote` 與 `POST /sales` 是兩次獨立讀取，中間店長改價完全可能。

POS 之所以安全，是因為它**一定會送 tenders**，而後端要求 Σ tenders = total：

`frontend/features/pos/tender.ts:210-231`
```ts
export function toTenders(plan, opts = {}) {
  const tenders = [];
  if (plan.storeCredit > 0) tenders.push({ tender_type: "STORE_CREDIT", ... });
  if (plan.cash > 0)        tenders.push({ tender_type: "CASH", ... });
  ...
  return tenders.length > 0 ? tenders : undefined;
}
```
只有全部腿都是 0 時才回 `undefined`，而那種情況 `validatePlan` 已先擋
（`:112-118`：`plan.cash + plan.storeCredit + plan.taiwanPay + plan.linePay !== total` → 不可結帳）。
所以價格一變，後端算出的 total 就對不上送來的 tenders → **422，fail-closed，客人不會被靜默多收**。

**但 API 合約允許省略 tenders**：

`backend/app/modules/sales/service.py:494-496`
```python
        if tenders is None:
            return [TenderInput(tender_type=TenderType.CASH, amount=total)]
```
任何省略 tenders 的呼叫端（舊版前端、腳本、未來的自助結帳）都會被收「後端當下重算的金額」，
沒有任何一方確認過那個數字。目前沒有這種呼叫端；這是合約層面的敞口，不是現行缺陷。

### 4.2　購物金負債的兩套加總：刻意雙軌，實測一致

`backend/app/modules/storecredit/repository.py:175-194` 同時提供
`ledger_total_outstanding()`（由帳本 group by contact 取正餘額加總）與
`total_outstanding()`（由 `store_credit_accounts.balance` 取正值加總）。
兩者刻意並存，正是 I-3 對帳的兩端。階段 3 已逐帳戶比對過
（`lucamp_manual` 316 個、`lucamp_sim` 138 個帳戶，**0 個不一致**），
且帳齡與 consumed 的計算一致排除了「已被沖正的原始 CREDIT」
（`:198-241` 的 `_not_reversed()`），不會把作廢收購的入帳算成有效負債。**判定：符合。**

### 4.3　發票證明聯：**正本是我們排版、補印是平台排版**——同一張發票兩種版面

**判定：不符合（升級為 P1-7）。**

- **正本**由前端本地組版，金額取本地拆分值：
  `frontend/app/(authed)/pos/page.tsx:1426` → `printEInvoice(invoice, sale, ...)` →
  `frontend/lib/agent.ts:83-85`
  ```ts
      sales_amount: invoice.net,
      tax_amount: invoice.tax,
      total_amount: invoice.total,
  ```
- **補印**整張版面由 Amego 產生、原樣轉印：
  `frontend/app/(authed)/sales/page.tsx:1093-1097` → `printRaw(data.base64_data)`，
  來源是 `backend/app/modules/einvoice/service.py:1113-1155` 的 `/json/invoice_print`。

而送給平台的 B2C payload 是 `SalesAmount = 含稅總額、TaxAmount = 0`
（`backend/app/modules/einvoice/amego.py:110-113`，依 `docs/24:49-54` 的規則）。

→ **同一張 B2C 發票，正本上的「銷售額／稅額」是我們算的 net/tax，補印上的是平台依其
紀錄印的（TaxAmount=0）。兩張紙很可能不一樣。** 這不再只是法規口徑問題，是可當場驗證的
版面不一致：印一張正本、再印一張補印，並排比對即知。

（B2B 無此問題：`sales_amount, tax_amount = int(invoice.net), int(invoice.tax)`，與本地一致。）

### 4.4　電子發票回執：兩階段設計正確，但 P0-2 會連帶打破它的核心保證

**判定：設計符合，惟受 P0-2 波及。**

`record_result` 的鎖序（sale → queue）與「回執事件永不回滾」的規則都寫得很明確：

`backend/app/modules/einvoice/router.py:355-366`
```python
    except EInvoiceResultNotApplicable as exc:
        # 規則：**回執事件一旦落庫、永不回滾**（Codex 第八輪）——未認領的回執本身就是
        # 值得稽核的異常證據；commit 保留事件後回 409（佇列/發票未被 service 變更）。
        await session.commit()
```

但 P0-2 的觸發點正是這條路徑的最後一步：F0501 成功回執 → `mark_invoice_voided()` →
`UPDATE sales SET invoice_status='VOID'` → 走到 `router.py:371` 的 `await session.commit()`
→ deferred trigger RAISE。

**連帶後果**：那次 commit 是「保留回執事件」的同一次 commit，它一起被回滾。
於是不只發票狀態收斂不了，**連「平台已核可作廢」這個稽核事件都留不下來**——
系統設計明文承諾的「回執事件永不回滾」在這個組合下不成立。這使 P0-2 比階段 2 判定的更嚴重。

---

## 需要你裁示／提供的事項

以下是這份稽核靠讀 code 和查資料庫**無法自行決定**的部分。

### ❶ 最要緊：P0-1／P0-2 要不要修、什麼時候修

兩者是同一個根因：2026-08-01 拆分銷售與發票生命週期時，漏改了兩支資料庫 guard 函式
（`sales_ledger_sale_debit_guard`、`sales_verify_store_credit_consistency`），
它們至今仍以 `invoice_status = 'VOID'` 判斷「銷售是否作廢」。

- 目前**沒有污染任何資料**（實測：0 筆作廢的購物金銷售、0 筆購物金整筆退貨）。
- 但只要店裡出現「用購物金付款的單要作廢」或「用購物金付款的單同月整筆退貨」，
  就會撞上——前者作廢直接失敗，後者發票狀態永久卡住且連稽核事件都留不下。
- 修正需要一支新的 alembic migration（`CREATE OR REPLACE FUNCTION` 改判 `sales.status`），
  屬**改程式碼**，本 session 的規則明令不做。

**我需要你決定：是否要我另開一個 session 來修這兩支函式。**

### ❷ 散裝成本尾差要怎麼記（P1-1）

98% 以上的散裝批收購成本除不盡件數，完售後認列成本與實際收購成本差 −2～+3 元。
這是業務規則問題，我不臆測：

- 選項 A：尾差落在最後一件（用差額法，和退款/點數同一套做法）——加總必然等於收購成本。
- 選項 B：接受每批幾元誤差，但至少在應用層明確 `round_ntd`，不要靠資料庫隱式捨入。
- 選項 C：現況不動，只補一條文件說明。

**我需要你選一個方向**（實作留待後續 session）。

### ❸ 三個我拿不到的外部事實

| 事項 | 我需要的東西 |
|---|---|
| P1-7 正本 vs 補印版面不一致 | **最快的驗證方式：找一張已開立的 B2C 發票，印一次正本、再印一次補印，把兩張紙並排拍給我看。** 只要「銷售額／稅額」兩欄一致，這條就解除 |
| P1-6 發票 UnitPrice 長小數 | Amego 對 `UnitPrice` 的小數位上限，以及它是否驗算 `Quantity × UnitPrice = Amount`。你手上的 Amego 技術文件或窗口回覆即可 |
| P1-4 進項發票稅額 | 你的供應商裡會不會出現免稅／零稅率的（例如農產品、部分服務）。若一律 5% 應稅，這條可以降級 |

### ❹ 兩個環境層面的提醒（不需裁示，但你應該知道）

- `.env` 的 `DATABASE_URL` 指向的 `lucamp` 已經是空殼庫（只剩 `alembic_version`，
  版本戳還停在非 head 的 `a9b0c1d2e3f4`）。實際跑的 backend 連的是 `lucamp_manual`。
  照 `.env` 連上去或對它跑 `alembic upgrade head` 都會壞。
- 現存 5 個庫有 5 個不同的 alembic 版本，只有 `lucamp_manual` 在 head。
  配合「trigger 只在建表 migration 安裝」這件事，**哪個庫有哪些守衛只能實查、不能推斷**。

### ❺ 本稽核的執行界線（讓你確認我沒越線）

- 沒有新增／修改／刪除 `backend/`、`frontend/`、`hardware-agent/` 的任何檔案（`git status` 僅 `?? docs/audit/`）。
- 沒有跑測試、沒有啟動或重啟任何服務、沒有下 migration。
- 對本機 PostgreSQL **只執行 `SELECT`**（`pg_database` / `pg_proc` / `pg_trigger` /
  `pg_stat_activity` / `alembic_version` 與各業務表的彙總查詢），查詢腳本放在 session
  scratchpad、不進 repo。若你認為連唯讀查詢都不該做，請告訴我，往後只讀原始碼。

---

## 階段 5：裁示與補掃（2026-08-22）

### 5.1　店主裁示

| 事項 | 裁示 | 依據 |
|---|---|---|
| P1-1 散裝成本尾差 | **不修**（選項 C：現況不動，本檔即為文件記錄） | 實測誤差規模見下 |
| 唯讀 SQL 查詢 | **允許**（本稽核往後仍可對本機 DB 執行 SELECT） | 2026-08-22 |
| `.env` 指向空殼庫 / 多庫版本不一 | 已知悉，本檔記錄即可 | 2026-08-22 |
| P1-7 正本 vs 補印版面 | 店主將實際印出兩張並提供照片後再判定 | 待照片 |

**P1-1 誤差規模實測**（支撐「不修」的裁示）：

| 資料庫 | 完售自有散裝批 | 該批收購成本合計 | 淨誤差 | 絕對誤差合計 | 單批最大 |
|---|---|---|---|---|---|
| `lucamp_sim` | 31 批 | 46,466 元 | **+13 元** | 49 元 | −6 元 |
| `lucamp_manual` | 2 批 | 974 元 | **+3 元** | 3 元 | +2 元 |

`lucamp_sim` 的 +13 元對照全期銷售總額 11,964,042 元 ＝ **0.0001%**；
且誤差正負皆有、不朝單一方向累積。**結論：規模不具帳務意義，維持現況。**
唯一保留的說明：這個取整發生在 PostgreSQL 的型別轉換，不在 `core/money.round_ntd()`，
與 CLAUDE.md §6 描述的路徑不同——日後若有人改動 `cost_snapshot` 的型別或算式，
不會有任何測試或守衛提醒。

### 5.2　補掃三處（宣告範圍內、階段 2–4 只做結構性確認者）

**(a) 客顯購物車與實際成交的金額比對：符合，且是逐欄 byte-exact。**

`backend/app/modules/sales/service.py:583-608`
```python
            visible = {
                "name": persisted.description,
                "qty": persisted.qty,
                "line_kind": persisted.line_kind.value,
                "unit_price": format(persisted.unit_price, "f"),
                "original_unit_price": ...,
                "discount_amount": format(persisted.discount_amount, "f"),
                "manual_discount_amount": format(persisted.manual_discount_amount, "f"),
                "line_total": format(persisted.line_total, "f"),
                "net_amount": format(persisted.net_amount, "f"),
            }
        ...
        if cart.snapshot.get("items") != actual_items:
            raise SignatureContentMismatch("實際成交商品、數量、單價或折扣與客顯購物車不一致")
```
收款拆分（`:610-615`）與內用桌號（`:619-625`）也一併比對。
客人螢幕上看到的每一個金額欄位，都必須與實際落盤的成交明細完全相同，否則整筆結帳失敗。

**(b) 報表的散裝成本口徑：與主帳務一致（P1-2 只有一個離群者）。**

`backend/app/modules/reports/service.py:446`（洞察報表）與 `:238`（庫存價值）都用
`round_ntd(acq_cost × 件數 ÷ 整堆件數)`。這在數學上與落盤的 `cost_snapshot`
（＝`per_piece_cost × qty` 經 `Numeric(12,0)` 取整）**同值**——同一個數量、同一個
ROUND_HALF_UP（PostgreSQL numeric 對正數即 half-away-from-zero）。故洞察／庫存價值／
`margin_breakdown` 三者一致。
P1-2 的離群者仍只有 `backend/app/modules/sales/repository.py:534`（完全不取整），
且只餵溢價建議引擎。

**(c) 標籤列印的價格來源：來自後端，但解析失敗會靜默印 0 元。**

`frontend/app/(authed)/acquisition/page.tsx:572,581`
```tsx
        await printLabel(code, data.name, parseNtd(data.listed_price) ?? 0);
        ...
        await printLabel(lot, data.name, parseNtd(data.unit_price) ?? 0);
```
價格取自後端重查的 `listed_price` / `unit_price`（權威），路徑正確。
但 `parseNtd` 只接受純整數字串，任何非預期格式（如 `"1000.00"`）會回 `null`，
`?? 0` 於是**印出一張 0 元的價格標籤**而不是報錯。目前後端序列化恆為整數字串，
不會發生；列為 P2-9（fail-open 到 0，方向錯誤——金額解析失敗應該擋，不該當 0）。

### 5.3　宣告範圍的涵蓋度結論

`docs/audit` 開頭列的 in-scope 七項，逐項都已到「讀完關鍵路徑原始碼 ＋ 對真實資料驗證」的程度：

| in-scope 項目 | 狀態 |
|---|---|
| 收購與寄售建單、定價與金額計算（買斷/寄售/散裝） | 完成（含 `_split_payout`、切結內容比對、散裝建堆） |
| 購物金帳本：寫入路徑、餘額計算、核銷 | 完成（單一寫入點 ＋ 5 個 DB trigger ＋ 454 帳戶逐一比對） |
| 寄售結算與抽成 | 完成（2,965 筆恆等式全數驗過） |
| 錢櫃現金異動 | 完成（687 個已關帳班別公式重算 0 例外） |
| 稅額計算、含稅未稅換算、四捨五入 | 完成（發現 P1-3 折讓稅差） |
| 收據／帳單／發票列印的金額來源 | 完成（明細聯、證明聯正本、證明聯補印、收購憑證聯、商品標籤五條路徑） |
| DB transaction 邊界與併發控制 | 完成（發現 P0-1／P0-2，並以 `pg_trigger`/`pg_proc` 實查佐證） |

**未再深入者，皆為宣告的範圍外**：前端樣式/文案、硬體代理印表機協定、認證授權（D-4 另案）、
作廢/更正流程的設計（F6.5 缺口只記一行，未展開）。

---

## 階段 6：追加裁示與其後果（2026-08-22）

### 6.1　P1-6 發票單價小數：裁示「填到小數第 2 位」

**店主裁示**：已與光貿確認，`UnitPrice` **可以填小數，填到第 2 位即可**。

→ P1-6 由「待確認」轉為**已確認的缺陷**：現行程式送出的是未限位數的 `Decimal` 除法結果
（最長 28 位有效位數），與裁示不符。

`backend/app/modules/einvoice/amego.py:90-96`
```python
        effective_unit = Decimal(line.net_amount) / Decimal(line.qty)
        items.append(
            {
                "Description": line.description[:_DESCRIPTION_MAX],
                "Quantity": line.qty,
                "UnitPrice": _decimal_str(effective_unit),
                "Amount": _decimal_str(Decimal(line.net_amount)),
```

**但「填到 2 位」會產生一個新的、無法靠取整消除的後果**，必須先釐清：

除不盡時，**任何** 2 位小數的單價乘上數量都不會等於實付金額：

| 實付 (Amount) | 數量 | 現行送出的 UnitPrice | 取 2 位小數 | 數量 × 2 位單價 | 差額 |
|---:|---:|---|---:|---:|---:|
| 200 | 3 | 66.66666666666666666666666667 | 66.67 | 200.01 | **+0.01** |
| 100 | 3 | 33.33333333333333333333333333 | 33.33 | 99.99 | **−0.01** |
| 500 | 7 | 71.42857142857142857142857143 | 71.43 | 500.01 | **+0.01** |
| 1000 | 6 | 166.6666666666666666666666667 | 166.67 | 1000.02 | **+0.02** |
| 333 | 2 | 166.5 | 166.50 | 333.00 | ±0 |
| 199 | 3 | 66.33333333333333333333333333 | 66.33 | 198.99 | **−0.01** |

而現行程式碼的註解明說，把單價寫成「小計÷數量」的**目的**就是要避免這種驗算落差：

`backend/app/modules/einvoice/amego.py:88-89`
```python
        # Amount（實收小計）為權威；折扣行的 UnitPrice 以小計÷數量表示（兩者一致，
        # 避免平台以 Quantity×UnitPrice 驗算時對不上）。
```

→ **仍待確認**：光貿是否會以 `Quantity × UnitPrice` 驗算 `Amount`。
- 若**不驗算**（以 `Amount` 為權威，`UnitPrice` 僅供版面顯示）：填 2 位小數即可，本項單純改取整。
- 若**會驗算且要求相符**：填 2 位小數解不掉，差 0.01～0.02 分會被退件——這時要處理的
  就不是取整，而是「一行多件又有折扣」這個結構本身。

其餘送往光貿的金額欄位不受影響：G0401 折讓的 `UnitPrice`／`Amount` 皆取整數 `net`、
`Quantity` 固定 1（`backend/app/modules/einvoice/amego.py:209-215`），不會產生小數。

### 6.2　P1-4 進項發票：裁示「要能勾選該單免稅」

**店主裁示**：供應商**可能會開免稅發票**；登記進項發票時應讓使用者**勾選該張是否免稅**。

→ P1-4 由「風險／待確認」轉為**已確認的功能缺口**。現況是無條件以本店 `tax_rate` 反推：

`backend/app/modules/purchasing/service.py:299-311`
```python
    def _invoice_fields(
        invoice: "InputInvoiceIn", tax_rate: Decimal
    ) -> dict[str, object]:
        """進項發票欄位＋稅額拆分（§6：net = round_ntd(total/(1+rate))、tax = total − net）。"""
        net, tax = split_tax_inclusive(Decimal(invoice.invoice_total), tax_rate)
```
輸入端也沒有任何課稅別欄位：

`backend/app/modules/purchasing/schemas.py:152-154`
```python
    invoice_number: str = Field(pattern=r"^[A-Z]{2}[0-9]{8}$")
    invoice_date: date
    invoice_total: NTDAmount
```

**影響面（已查證，範圍很小）**：`invoice_net` / `invoice_tax` 目前**只有一個消費端**——
採購頁的顯示：

`frontend/app/(authed)/purchasing/page.tsx:669-670`
```tsx
                      {money(r.invoice.invoice_total)}｜未稅 {money(r.invoice.invoice_net)}／稅{" "}
                      {money(r.invoice.invoice_tax)}
```
全庫搜尋確認**沒有任何報表把 `invoice_tax` 加總**（系統目前不產進項稅額申報數字）。
所以免稅發票目前造成的錯誤僅止於「採購頁上那一行顯示的未稅/稅額是錯的」，
還沒有流入任何彙總或申報用途。

**尚待釐清（不臆測）**：同一張進項發票是否可能**部分應稅、部分免稅**。
若可能，「整張打勾」的模型就不夠用；若你的供應商一張發票只會是單一課稅別，打勾即足夠。

### 6.3　P1-7 證明聯正本 vs 補印

店主將實際印出正本與補印各一張後提供照片，屆時比對「銷售額／稅額」兩欄再結案。
狀態維持「待確認」。

---

## 階段 7：P1-7 結案、其餘裁示（2026-08-22）

### 7.1　P1-7（正本 vs 補印版面不一致）：**撤銷，不成立**

**證據一：證明聯版面根本不印銷售額／稅額。**

`hardware-agent/agent/drivers/escpos_receipt.py:349-388`
```python
    def print_einvoice(self, invoice: InvoicePayload) -> None:
        """列印電子發票證明聯（附件一格式一；記載順序固定、不得增刪/變更）。

        順序：營業人識別標章 → 「電子發票證明聯」 → 年期別 → 字軌號碼 →
        交易日期時間 → 隨機碼/總計 → 賣方（買方）統編 → 一維條碼 → 左右二維條碼。
        """
        ...
        out += _line(f"隨機碼:{invoice.random_code} 總計:{invoice.total_amount}")  # 6/7
```
記載項目由「電子發票實施作業要點」附件一格式一規定、不得增刪，其中**沒有**銷售額與稅額欄。

**證據二：店主提供的實機照片（2026-08-22，發票 ZA-10062325）。**
兩張證明聯的可見欄位為：店名／「電子發票證明聯」／115年07-08月／ZA-10062325／
`2026-08-22 21:17:19`／`隨機碼:8374 總計:110`／`賣方12345678`／一維條碼／左右二維條碼。
**兩張皆無銷售額、稅額欄。**

→ 前端 `frontend/lib/agent.ts:83-85` 傳給代理的 `sales_amount` / `tax_amount`
是**未被使用的欄位**（`InvoicePayload` 有定義但版面不引用），因此
「正本印本地拆分值、補印印平台值」的不一致**不可能發生在紙上**。P1-7 撤銷。

殘留一條 P2-10（純冗餘、無帳務風險）：`printEInvoice` 仍在 payload 帶
`sales_amount` / `tax_amount`，代理端從不渲染；此為未使用欄位。

### 7.2　照片衍生的新觀察（待店主確認，非本次稽核範圍內的金額問題）

照片中兩張證明聯的字軌、開立時間、隨機碼、總計完全相同，且**兩張皆未見「補印」字樣**。
系統的補印邏輯建立在「補印會被加註」這個假設上：

`backend/app/modules/einvoice/service.py:1131-1135`
```
        **正本還是補印，由「印出來過沒有」決定**（電子發票實施作業要點 §26）：
        證明聯以列印一次為限，從未印出（例：開立成功但回應斷線）時那一次還沒用掉，
        要印**正本**；已印過才印補印——補印會加註「補印」二字，且依法須併同原聯
        才能兌獎，誤用等於給客人一張兌不了獎的紙。
```
**待店主確認**：這兩張是「正本＋補印」還是「兩張正本」。
- 若其中一張是補印卻未加註 → 客人可能持有兩張外觀相同的證明聯，重複兌領獎金的責任
  歸營業人，需另案處理（涉及 Amego 的 `reprint` 參數行為，非本稽核範圍）。
- 若兩張都是正本 → 表示 `proof-printed` 的標記未生效，「列印一次為限」的計數沒起作用。

### 7.3　追加裁示

| 事項 | 裁示 | 後續 |
|---|---|---|
| P1-6 光貿是否驗算 `數量 × 單價` | **不驗算**（以 `Amount` 為權威） | 「填到小數第 2 位」可行，改為取整至 2dp 即可 |
| P1-4 進項發票是否可能混稅別 | **不會**（一張發票單一課稅別） | 「整張打勾免稅」的模型足夠 |
| P0-1 / P0-2 | **核准修復** | 見下 |

### 7.4　本檔的性質變更

自 2026-08-22 店主核准修復 P0-1／P0-2 起，本 session 的「唯讀、不改任何實作」約束
由店主明示解除（僅就核准的修復範圍）。稽核結論本身不再變動；
後續修復以獨立分支、依 `CLAUDE.md` 的 TDD 與四道門進行，修復內容不回寫本檔的發現段落。
