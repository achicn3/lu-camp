# ADR-013：銷售與發票的生命週期分離

- **Status**: Accepted（2026-08-01 店主提出並核准；本 ADR 記錄「變更 A」已實作合併，「變更 B」與後續待辦見文末）。
- **Context**:
  - 原設計沒有「銷售已作廢」這個狀態：`SaleStatus` 只有 `COMPLETED` / `RETURNED`。因此 `void_sale()` 以
    **`sales.invoice_status = VOID`** 表示「這筆銷售作廢了」，且是**無條件**設定的。
  - 後果一（既存缺陷）：**電子發票關閉時（本專案預設）該筆交易根本沒有發票**，卻仍被標成「發票已作廢」。
    狀態欄位說了一件與事實不符的事。
  - 後果二（既存缺陷）：報表、清單與守衛共 **21 處**以 `Sale.invoice_status != VOID` 過濾「已作廢銷售」
    （其中 15 處是 `sales/repository.py` 內同一行的查詢條件）。判斷「交易算不算數」卻讀了發票欄位。
  - 觸發點：新需求「**同月整筆退貨要作廢發票**」（見 §7 不變量 5 修訂與變更 B）。一旦退貨也會讓發票變成
    `VOID`，`invoice_status == VOID` 就同時代表兩件事——已作廢的銷售、與有效但全退的銷售。那 21 處會
    **把已全退的有效交易從報表中排除**，且資料遷移將無從分辨兩者。
- **Decision**:
  - **`sale.status` 是「這筆銷售是否有效」的唯一事實來源**，新增 `VOIDED`。報表、清單與守衛一律以
    `sale.status != VOIDED` 判斷，**不得**再以 `invoice_status == VOID` 代替。
  - **`invoice.status` 只描述發票自身**（PENDING / ISSUED / VOID_PENDING / VOID / ALLOWANCE）。
  - **新增 `invoice.void_reason`**：`SALE_VOID`（整筆銷售作廢）／`FULL_RETURN`（銷售有效但全數退貨）／
    `CORRECTION`（開立內容有誤重開）。同樣是「作廢」，帳務意義不同，報表與稽核必須能分辨。
  - `void_sale()` 只設 `sale.status = VOIDED`；**發票狀態交由 `void_invoice_for_sale()` 依「實際上有沒有
    發票、平台是否已核可」決定**——沒有發票就不動 `invoice_status`。
  - 於是兩種情境可被正確表達：

    | 情境 | sale.status | invoice.status | invoice.void_reason |
    | --- | --- | --- | --- |
    | 打錯單整筆作廢 | `VOIDED` | `VOID` | `SALE_VOID` |
    | 交易有效、客人全退 | `RETURNED` | `VOID` | `FULL_RETURN` |
    | 交易有效、部分退 | `COMPLETED` | `ISSUED`（另有折讓單） | — |
- **遷移的時機是決策的一部分**：在本次變更**之前**，`invoice_status = 'VOID'` ⟺ 銷售已作廢，**一對一、無歧義**，
  回填只是一行 UPDATE。等「退貨作廢發票」上線後，VOID 會有兩種來源，回填就得靠 `void_reason` 或 join 退貨單
  反推。**故先拆分、再做功能**，順序不可顛倒。同一次遷移也把「已作廢但從未開過發票」的單之 `invoice_status`
  由被污染的 `VOID` 還原為 `NOT_ISSUED`。
- **Alternatives**:
  - **新增 `SaleInvoiceStatus.VOID_BY_RETURN` 當過渡**：駁回。它延續了「用發票狀態表達銷售狀態」的根本問題，
    而且**並不省工**——那 21 處為了區分兩種 VOID 仍須逐一檢視，等於做一樣的事卻多留一個之後要遷移掉的列舉值，
    並讓上述無歧義的回填時機一去不返。
  - **移除 `sales.invoice_status`、一律由 `invoices` 表推導**：駁回（本次）。該欄位已在 API 合約與 UI 顯示中，
    移除的變更面遠大於收益；保留為**顯示用的反正規化欄位**即可，事實來源責任已由 `sale.status` 承擔。
- **Consequences**:
  - `POST /sales/{id}/void` 的回應語意改變：作廢反映在 `status: "VOIDED"`，`invoice_status` 不再被污染。
    既有測試中斷言舊語意者已更新（非弱化——保護意圖不變，只是斷言正確的欄位）。
  - `SaleStatus` 新增列舉值屬**相加**變更，OpenAPI 合約不破壞既有欄位；前端僅 1 處判斷與 1 處標籤需調整。
  - 前端「已作廢」的判斷改為 `sale.status === "VOIDED"`。
- **已知待辦（本次刻意不做，避免擴大變更面）**：
  1. `RETURNED` 未區分 `PARTIALLY_RETURNED` / `FULLY_RETURNED`——目前部分退貨仍停在 `COMPLETED`，語意不精確，
     但無邏輯依賴。
  2. `InvoiceStatus.ALLOWANCE` 未區分 `PARTIALLY_ALLOWED` / `FULLY_ALLOWED`——多次部分折讓全部落在同一狀態，
     屬資訊損失。
  3. 折讓／作廢的外部結果目前以「佇列 PENDING＋已認領」隱含表達「結果未知（對帳中）」，缺少顯式狀態，
     不利 UI 與報表觀測（變更 B 處理）。
