# 14 — 電子發票 MIG / Turnkey 對照（G1 閘門研究成果）

> 本檔為 **Phase 3 電子發票（T13）動工前置閘門 G1** 的查證成果，供 T13 實作引用。
> ⚠️ **本檔是「對照骨架」，非完整 XSD**。T13 動工前仍須下載官方 **Turnkey 3.9 完整手冊**與 **MIG 4.0/4.1** 規格，逐節讀目錄設定、回執命名與格式、錯誤碼、各欄位**資料長度與 Enum**，依實際 XSD 實作，**不得憑本摘要硬寫**。
> 來源：財政部電子發票整合服務平台官方 PDF（見文末）。查證日：2026-06。

## 0. 關鍵更正（閘門擋下的「憑記憶硬寫」陷阱）

| 項目 | 專案原文件（錯／過時） | 實際現行（採用） |
|---|---|---|
| Turnkey 版本 | v3.2（僅安裝程式編號） | **安裝程式 3.2.1／說明書 Ver 3.9／MIG 4.1——三者各自獨立編號、互相吻合（詳 §5.1）** |
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

- 產生的 MIG XML 投入 **Turnkey 的存證目錄 `UpCast/B2SSTORAGE/<訊息類型>/SRC/`**（MIG 4.0+ 已把 B2B/B2C 存證整併為 **B2S**；檔名無限制、需 UTF-8），Turnkey 排程自動上傳平台；對應本專案架構之「MIG XML 拋出目錄」（docs/02 高階架構圖）。**設定為 XML（`einvUserConfig.xml`），非 `turnkey.ini`**；目錄細節見 §5。
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

## 5. Turnkey 部署環境、流程與憑證盤點（2026-06 安裝包/手冊實查）

> 本節為**實際下載安裝包 `5420.zip`（內含 `EINVTurnkey_setup_3.2.1.tar.gz`）＋說明書 321.pdf（Ver 3.9）＋MIG 4.1（5380.pdf）**逐項拆解後的盤點，供「發票收尾階段」（見 docs/07）動工引用。**結論：採同機 Linux x86-64 佈署（選項 A）。**

### 5.1 版本相容性結論（已確認）

- 安裝程式實體版本 **Turnkey 3.2.1（gateway 3.1.3）**；說明書 **Ver 3.9**；MIG **Ver 4.1（2025-10-29）**。三個編號各自獨立、彼此吻合。
- **Turnkey 3.2.x 只送 MIG 4.1**（`VERSION.md`：「3.2.0 只得傳送 v41 版發票」；gateway 3.1.3 對 F0401/E0501/E0504 做 XSD 驗證）。與 G1 拍板的 **F0401/F0501/F0701 + G0401/G0501** 完全相容；五種訊息均存在於 MIG 4.1 規格。
- ⚠️ **安裝後必須在「目錄設定 → 來源訊息版本」設為 V4.1**，否則上傳直接報錯。

### 5.2 系統需求（321.pdf〈壹、三〉）

| 項目 | 需求 |
|---|---|
| 作業系統 | Windows（Win10/2016+）、**Linux（Ubuntu 18.04+/RedHat ES7+，64-bit）**、FreeBSD 12.2+。**不支援 macOS／Apple Silicon**（安裝包為 Linux x86-64 專屬：JavaFX `-linux` 原生 jar、`LD_LIBRARY_PATH`＋`so/x64`、`uname -m` 無 arm64 分支）。 |
| 硬體 | 四核心 2.0GHz＋、**RAM 32GB＋**、可用空間 **80GB＋** |
| Java | **OpenJDK 17**（自行下載 JRE 放進 Turnkey 目錄下 `jre/`；安裝包附的 `jre/` 為空目錄。可設 `JAVA_HOME`＋建 `javahome` 標記檔改用外部 JRE） |
| 資料庫 | PostgreSQL 11.7+／Oracle／MySQL／MSSQL／MariaDB，或內建 H2。**若沿用既有 PostgreSQL，Turnkey 必須建在獨立的 database。** |
| 設定檔 | **XML，非 `turnkey.ini`**：`einvTurnkeyConfig.xml`（平台 SFTP/HTTPS 端點，已內建免改）＋ `einvUserConfig.xml`（DB 連線、`def-path` 工作目錄、`erp-in-box-path` 回執 inbox、`data-keep-days`、`execute-environment=T` 測試區）。 |
| Headless | **可無 GUI 運行**：`run_cmd.sh`（文字模式）＋ `run_monitor_cmd.sh`；GUI（`run_ui.sh`）才需 X11＋中文字集。日常排程 `run_start.sh`。→ 適合伺服器無桌面環境。 |

### 5.3 目錄與流程鏈（321.pdf〈柒〉）

- **拋檔點（我方寫入）**：`EINVTurnkey/UpCast/B2SSTORAGE/<訊息類型 如 F0401>/SRC/`（檔名不限、UTF-8）。
- **流程鏈**：`UpCast`（轉檔）→ `Pack`（加 SIG＋**簽章**）→ `SendFile`（SFTP 2222 / Web API 上傳）→ `Receivefile`（下載 `ProcessResult`/`SummaryResult`，含 **E0501 配號檔**、E0502/E0503 進項存證、E0504 中獎清冊）→ `Unpack`（解簽）→ `Downcast`。處理完移 `BAK/日期/時`，失敗移 `ERR/`。
- **狀態查詢**：寫入 DB 表 **`TURNKEY_MESSAGE_LOG` / `TURNKEY_MESSAGE_LOG_DETAIL`**（成功 `G`／失敗 `E`）；錯誤另記 `TURNKEY_SYSEVENT_LOG`。我方後端以**唯讀**方式查此表取得上傳狀態與 ProcessResult（架構上視為外部系統，不納入 router→service→repo 模組）。

### 5.4 憑證來源與流程（線上申請說明 + 〈製作軟體憑證〉）

1. **工商憑證（IC 卡）＋讀卡機** — 向**經濟部工商憑證管理中心（MOEACA）**申請；為一切源頭（平台帳號註冊 Step2 需插卡輸統編＋PIN）。安裝包內 `pkcs11wrapper` 即讀此卡。
2. **平台帳號**：登入 einvoice.nat.gov.tw → 工商憑證註冊帳號 → 註冊主憑證 → 申請 Turnkey（正式區＋驗證區，限**非中國大陸 IP**）→ 審核**約 3 個工作天** → 寄「核定通知信」。
3. **簽章憑證（擇一）**：直接用工商憑證（Card），或 Turnkey「製作軟體憑證」：產金鑰 → 產 CSR → **上傳 CSR 至憑證管理中心取得簽署 CER** → 組 PFX → 在「憑證管理」登錄。自動化排程建議用軟體憑證（免每次插卡）。
4. **SSL CA**（連平台 TLS）已附在 `cert/`，免申請。
5. 客服：**02-89782365 / e-inv@hibox.hinet.net**。

### 5.5 架構決定

- **選項 A（採用）**：Turnkey 與門市後端**同跑一台 Linux x86-64 主機**，drop 目錄 `UpCast/B2SSTORAGE/<msg>/SRC` 走**本機路徑**，後端直接寫檔、Turnkey 排程撿走，狀態回讀同機 `TURNKEY_MESSAGE_LOG`。**不需跨機器、不需網路檔案系統**，故障點最少。
- 放棄「轉 MacBook」（Turnkey 不支援 macOS/Apple Silicon）。

### 5.6 待確認項（動工前釐清）

1. **主憑證政策**：是否一定要工商憑證、能否用其他組織憑證 → **待打客服 02-89782365 確認**後再申請。
2. **防火牆對外 IP 開通**：需向平台申請開通我方對外固定 IP（SFTP 2222＋HTTPS），且為非中國大陸 IP（`readme.txt` 明載）。

## 來源

- MIG V4.1（官方 PDF）：https://www.einvoice.nat.gov.tw/static/ptl/ein_upload/download/5340.pdf
- Turnkey 使用說明書 Ver 3.9（官方 PDF）：https://www.einvoice.nat.gov.tw/static/ptl/ein_upload/download/321.pdf
- Turnkey 3.2.1 安裝包（Linux，內含 DBSchema 與線上申請說明）：https://www.einvoice.nat.gov.tw/static/ptl/ein_upload/download/5420.zip
- MIG 4.1（本次實查 PDF）：https://www.einvoice.nat.gov.tw/static/ptl/ein_upload/download/5380.pdf
- MIG 快速上手：https://www.einvoice.nat.gov.tw/ptl007w/1692324517106
- 財政部電子發票整合服務平台：https://www.einvoice.nat.gov.tw/
