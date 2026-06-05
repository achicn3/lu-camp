# 14 — 電子發票 MIG / Turnkey 對照（G1 閘門研究成果）

> 本檔為 **Phase 3 電子發票（T13）動工前置閘門 G1** 的查證成果，供 T13 實作引用。
> ⚠️ **本檔是「對照骨架」，非完整 XSD**。T13 動工前仍須下載官方 **Turnkey 3.9 完整手冊**與 **MIG 4.0/4.1** 規格，逐節讀目錄設定、回執命名與格式、錯誤碼、各欄位**資料長度與 Enum**，依實際 XSD 實作，**不得憑本摘要硬寫**。
> 來源：財政部電子發票整合服務平台官方 PDF（見文末）。查證日：2026-06。

## 0. 關鍵更正（閘門擋下的「憑記憶硬寫」陷阱）

| 項目 | 專案原文件（錯／過時） | 實際現行（採用） |
|---|---|---|
| Turnkey 版本 | v3.2 | **Ver 3.9（2025-02 發布）** |
| B2C 存證開立訊息 | `C0401` | **`F0401`（平台存證開立發票）** |
| B2C 存證作廢 | `C0501` | **`F0501`** |
| B2C 存證註銷 | `C0701` | **`F0701`** |
| 折讓（開立／作廢） | `B0401`/`D0401`、`B0501`/`D0501` | **`G0401`（開立折讓）／`G0501`（作廢折讓）** |

**原因**：MIG **V4.0（2024-05-30）** 將「存證類」發票訊息整併——刪除 `A0401`/`C0401`→新增 `F0401`；刪除 `A0501`/`C0501`→`F0501`；刪除 `C0701`→`F0701`；刪除 `B0401`/`D0401`→`G0401`；刪除 `B0501`/`D0501`→`G0501`。`F0401` 同時**移除 `Attachment` 與 `CheckNumber`（發票檢查碼）**，`CarrierId1/CarrierId2` 長度調整。

> 本店為**自建 Turnkey 的存證營業人**，開立走 **F0401**。常見網路範例多為舊版 C0401，不可照抄。

## 1. F0401 訊息樹（取自下載之官方 MIG 4.1 PDF）

```
Invoice
├─ Main  [M]
│   ├─ InvoiceNumber [M]            發票字軌號碼
│   ├─ InvoiceDate [M]              開立日期（民國年格式，依 XSD）
│   ├─ InvoiceTime [M]             開立時間
│   ├─ Seller [M]  { Identifier[M], Name[M], Address[M], PersonInCharge?, TelephoneNumber?, FacsimileNumber?, EmailAddress?, CustomerNumber?, RoleRemark? }
│   ├─ Buyer  [M]  { Identifier[M], Name[M], Address?, PersonInCharge?, TelephoneNumber?, FacsimileNumber?, EmailAddress?, CustomerNumber?, RoleRemark? }
│   ├─ BuyerRemark?  MainRemark?  CustomsClearanceMark?  Category?  RelateNumber?
│   ├─ InvoiceType [M]             InvoiceTypeEnum：07 一般稅額 / 08 特種稅額
│   ├─ GroupMark?
│   ├─ DonateMark [M]              1=捐贈 / 0=非捐贈（V4.0 起明定）
│   ├─ CarrierType?  CarrierId1?  CarrierId2?     載具（CarrierTypeEnum）
│   ├─ PrintMark [M]               列印註記 Y/N
│   ├─ NPOBAN?                     捐贈碼（DonateMark=1 時，3–7 碼數字）
│   ├─ RandomNumber?               防偽隨機碼 **4 位數值**（V4.0 起，非 "AAAA"）
│   └─ BondedAreaConfirm?  ZeroTaxRateReason?  Reserved1?  Reserved2?
├─ Details  [M]
│   └─ ProductItem [1..9999] { Description[M], Quantity[M], Unit?, UnitPrice[M], TaxType[M], Amount[M], SequenceNumber[M], Remark?, RelateNumber? }
└─ Amount  [M]
    ├─ SalesAmount [M]  FreeTaxSalesAmount [M]  ZeroTaxSalesAmount [M]
    ├─ TaxType [M]  TaxRate [M]  TaxAmount [M]  TotalAmount [M]
    └─ DiscountAmount?  OriginalCurrencyAmount?  ExchangeRate?  Currency?
```

> 型態提醒：自 MIG V3.2 起 `TaxAmount`/`TotalAmount`/`DiscountAmount`（及折讓的 `Tax`/`TaxAmount`/`TotalAmount`）由 `long` 改為 **`decimal`**。與本專案 §6「Decimal、整數元、ROUND_HALF_UP」一致；序列化時以整數元輸出，避免角分。

## 2. 我方 invoice 模型 → F0401 對照（T13 實作骨架）

| 我方欄位（規劃） | F0401 元素 | 備註 |
|---|---|---|
| `invoice_no`（字軌+號碼） | `Main/InvoiceNumber` | 字軌配號管理另議 |
| `issued_at` 拆 date/time | `InvoiceDate` / `InvoiceTime` | 民國年/時間格式依 XSD |
| 店家統編/店名/地址（`settings`/store） | `Seller/Identifier·Name·Address` | |
| `buyer_tax_id`（B2B）或 `"0000000000"`（B2C） | `Buyer/Identifier` | **B2C 買方填 10 個 "0"** |
| 買方名稱 | `Buyer/Name` | B2C 可填預設值，依 XSD |
| `invoice_type`（預設 07） | `InvoiceType` | InvoiceTypeEnum |
| `carrier_type`/`carrier_id` | `CarrierType`/`CarrierId1`/`CarrierId2` | 手機條碼 `3J0002`、自然人憑證 `CQ0001`… 對照 CarrierTypeEnum |
| 捐贈旗標 / 捐贈碼 | `DonateMark`(1/0) / `NPOBAN` | |
| `print_mark` | `PrintMark` | 用載具時預設 N（不印證明聯） |
| `random_number` | `RandomNumber` | **4 位數值** |
| 每筆 `sale_line` | `Details/ProductItem/*` | 品名/數量/單價/TaxType/金額/序號 |
| `net`/`tax`/`total`（`core/money.split_tax_inclusive`） | `Amount/SalesAmount`·`TaxAmount`·`TotalAmount`·`TaxRate` | 稅在**總額層級**推算一次（§6），不逐項算稅 |

> 折讓（退貨且原銷售已開票）走 **G0401**（開立折讓）/**G0501**（作廢折讓），明細允許單價/數量/金額/營業稅額為負；欄位另於 T13 依 G0401 XSD 對照。

## 3. Turnkey 拋檔 / 回執流程（待 T13 依 3.9 手冊精確化）

- 產生的 MIG XML 投入 **Turnkey（Ver 3.9）`turnkey.ini` 指定之待上傳/輸入目錄**（存證目錄、B2B 交換目錄、發票配號訊息目錄等），Turnkey 自動上傳平台；對應本專案架構之「MIG XML 拋出目錄」（docs/02 高階架構圖）。
- 回執分兩層：
  - **`ProcessResult`**：逐筆上傳處理結果與錯誤碼。
  - **`SummaryResult`**：彙總，用於偵測漏傳。
- 若 Turnkey 在上傳前出錯，平台收不到該批 ProcessResult/SummaryResult → 需看 Turnkey 訊息 log、依錯誤碼修正後重送。對應 docs/04 之 `/einvoice/queue/{id}/retry`、`/einvoice/process-results`，狀態維護於 `einvoice_upload_queue`（`PENDING/UPLOADED/FAILED`）。
- **離線韌性**：產 XML 為本地檔案動作，斷網不影響開立；上傳由 Turnkey 連線恢復後處理（ADR-004）。

## 4. T13 動工前仍須完成（閘門未解項）

1. 下載 **Turnkey 3.9 完整使用說明書**：逐節讀目錄設定、回執檔**命名規則與檔案格式**、**錯誤碼表**。
2. 自 MIG 4.1 PDF 取齊 **F0401 / F0501 / F0701 / G0401 / G0501** 各欄位的**資料長度、必要性、Enum**（`InvoiceTypeEnum`、`TaxTypeEnum`、`CarrierTypeEnum`、`DonateMarkEnum`、`ZeroTaxRateReasonEnum` 等）。
3. 確認 `InvoiceDate`/`InvoiceTime` 的**民國年與格式**、`RandomNumber` 產生規則、字軌配號流程。
4. 以上落地為後端 schema 與 XML 序列化測試（含 `net+tax=total` 不差一元之金額測試）。

## 來源

- MIG V4.1（官方 PDF）：https://www.einvoice.nat.gov.tw/static/ptl/ein_upload/download/5340.pdf
- Turnkey 使用說明書 Ver 3.9（官方 PDF）：https://www.einvoice.nat.gov.tw/static/ptl/ein_upload/download/321.pdf
- MIG 快速上手：https://www.einvoice.nat.gov.tw/ptl007w/1692324517106
- 財政部電子發票整合服務平台：https://www.einvoice.nat.gov.tw/
