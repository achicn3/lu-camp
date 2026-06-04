# 01 — 系統需求規格（SRS）

技術識別字（資料表、欄位、API、角色）一律英文；說明用中文。模組邊界對應 `05-project-structure.md` 的 `app/modules/*`。

## 角色與權限

| 角色 | 說明 | 代表權限 |
|------|------|----------|
| `MANAGER`（總部/管理者） | 老闆/店長，現階段等同總部 | 全部，含跨店彙整報表、設定、PII 解密查看、權限管理 |
| `CLERK`（店員） | 一般門市人員 | 收購、銷售、寄售操作、現金開/結帳；改價與作廢需權限或留痕 |

- 權限以 RBAC 實作，敏感動作（作廢發票、改價、現金調整、PII 查看）一律寫 `audit_log`。

---

## 模組需求

### A. Auth / 使用者與權限
- 帳號密碼登入（密碼雜湊），JWT 短效 token + refresh。
- 角色與權限指派；停用帳號。
- 所有登入、權限變更寫稽核。

### B. Contacts / 聯絡人主檔（統一）
- 單一 `contact` 主檔，可同時具備多重角色：`MEMBER`（買方會員）、`SELLER`（賣方）、`CONSIGNOR`（寄售人）。同一人多次往來只建一筆。
- 欄位：姓名、電話、（會員）點數與消費紀錄、（賣方/寄售人）`national_id`（加密）、聯絡資訊、來源備註。
- 由店員協助建檔。
- **收購/寄售入庫時，姓名與 `national_id` 為必填**（來源登記）。
- `national_id` 加密儲存、限 `MANAGER` 解密查看、查看寫稽核。**不可明文/部分搜尋**；另存 `national_id_blind_index = HMAC(national_id, 金鑰)` 做**精確去重比對**（避免同一賣方重複建檔），日常找人以姓名/電話查詢。

### C. Inventory / 庫存（四型態）
**成色/等級列舉 `grade` = S/A/B/C/D/E（六級，可設定用語）**：S=熱門/搶手、A=近全新/精品、B=良好、C=普通、D=較差出清、E=散裝（秤斤/整袋收）。其中 **S–D 為序號單品，E 為散裝批**。

庫存追蹤型態：
1. **數量型 SKU（`catalog_product`）**：飲料、全新商品。以數量管理，ownership 一律 `OWNED`。
2. **序號化單品（`serialized_item`，等級 S–D）**：每件唯一，含：
   - `ownership_type`：`OWNED`（二手買斷）/ `CONSIGNMENT`（寄售，如帳篷）
   - `grade`：S/A/B/C/D
   - `photos`：選填、可多張
   - `OWNED`：`acquisition_cost`（收購成本）
   - `CONSIGNMENT`：`consignor_id`、`commission_pct`（預設 50）
   - `item_code`（唯一條碼，**建檔當下產生即固定、永不變**，與 POS 結帳掃描同一套碼；入庫時列印標籤、事後可隨時補印）、`listed_price`、`status`、`source_contact_id`、`intake_date`、`sold_date`
   - `status`：`IN_STOCK → SOLD`／`RETURNED_TO_CONSIGNOR`／`WRITTEN_OFF`（已 `SOLD` 不可再售）。
3. **散裝批（`bulk_lot`，等級 E）**：分批向客人收購，**每堆一筆獨立記錄、各自固定每件均一價**（A 堆價≠B 堆價）。含 `lot_code`（唯一條碼，**建檔當下產生即固定、永不變**，與 POS 掃描同碼）、整堆收購成本、件數（入庫記錄、可估算）、剩餘件數、均一價、狀態。售出按該堆均一價扣一件；每件成本 = 整堆成本 ÷ 件數。
- 庫存查詢、上架/下架、改價（改價留痕）。
- **商品條碼列印**：序號品 `item_code`、散裝堆 `lot_code` 採 **1D Code 128**，內容只放識別碼；建檔當下產生即固定、永不變，與 POS 結帳掃描為同一套碼。庫存頁/商品詳情可隨時**補印條碼**（補印須留稽核）。

### D. Acquisition / 收購鑑價入庫
- 一張 `acquisition` 單據對應一次入庫事件：`type = BUYOUT | CONSIGNMENT | BULK_LOT`、賣方/寄售人 `contact`（必填姓名+national_id）、經手店員、日期。
- 流程：選/建聯絡人 → 鑑價 → 確認。
  - `BUYOUT`（序號買斷，S–D）：逐件分級、選填拍照、定收購價；當場**付現金**（現金出帳），建立 `serialized_item(ownership=OWNED)`，每件產 `item_code` 並列印條碼標籤。
  - `CONSIGNMENT`（寄售）：逐件，不付現，定拋售價與抽成（預設 50），建立 `serialized_item(ownership=CONSIGNMENT)`，列印標籤。
  - `BULK_LOT`（E 級散裝）：按重量/整袋收購，記錄整堆收購成本、件數（可估算）、**該堆每件均一價**；當場**付現金**（現金出帳），建立一筆 `bulk_lot`，產 `lot_code` 並可列印整堆標籤。
- **建檔（品牌/品名主檔 + autocomplete）**：
  - `brand` 為輕主檔（店員可當場新增）；`product_model` 為型號主檔（品牌 + 品名/型號 + 分類）。
  - 收購時品名「**自由輸入 + 優先 autocomplete 既有 `product_model`**」；選既有 → 自動帶入品牌/分類與該型號的收購/售出價歷史；輸入全新 → 順手建一筆 `product_model`。
  - 入庫的 `serialized_item`/`bulk_lot`/`catalog_product` 帶 `brand_id`；`serialized_item` 可選 `product_model_id`（供型號層級的歷史與報表）。
- **定價輔助（定價計算機）**：主算法用目標毛利率 `建議售價 = round_ntd(收購價 ÷ (1 − margin_pct/100))`，為**含稅整數元**；`default_margin_pct` 放 `settings`（整數百分數，預設 45），`margin_pct` 限 0–99。同時顯示該型號歷史售價當參考；店員可手動覆蓋任一數字（毛利率或建議售價）。價格歷史由既有 `acquisition`/`sale` 紀錄依 `product_model_id` 聚合取得。
- **條碼列印**：識別碼（`item_code`/`lot_code`）一旦建檔即固定不變。入庫時可批次列印標籤（`/acquisitions/{id}/print-labels`）；序號品與散裝堆事後皆可隨時**補印**（`/serialized-items/{id}/print-label`、`/bulk-lots/{id}/print-label`，補印留稽核）。標籤以 1D Code 128 編碼識別碼。
- 寫入 `stock_movement`（IN）。

### E. Consignment / 寄售管理
- 寄售品賣出時自動產生 `consignment_settlement`（`commission_pct` 為整數百分數，預設 50）：`gross=售價`、`commission_amount=round_ntd(售價 × commission_pct / 100)`、`payout_amount=售價−commission_amount`、`status=PENDING`。
- 付款給寄售人：標記 `PAID`，產生現金抽屜出帳，寫稽核。
- 未售出處理：可「退回寄售人」（`RETURNED_TO_CONSIGNOR`，stock_movement OUT）或調整拋售價。
- 報表：寄售在庫、應付未付清單、已實現抽成收入。

### F. Purchasing / 供應商與採購（數量型商品）
- `supplier` 主檔。
- 採購單 `purchase_order` → 收貨 `goods_receipt` → 增加 `catalog_product` 數量、寫 `stock_movement`(IN)。
- 補貨點/低庫存提醒（可設定 reorder point）。
- 向供應商進貨之進項憑證資訊欄位（供會計使用）。

### G. Sales / POS 銷售
- 純現金結帳。掃 `item_code`（序號品）或選 `catalog_product`（數量品）加入購物車；E 級散裝則選/掃 `bulk_lot`，以該堆均一價售出（可一次多件），扣 `remaining_qty`。
- 結帳：計算金額與稅、開啟錢櫃、列印收據。
- **商品明細聯（店員當場選擇是否列印）**：一張完整銷售明細（交易序號、日期、店家/統編、逐項品名/數量/單價/小計、折扣、總計、付款方式），供需要的客人索取。
  - **結帳付款完成後，結帳完成畫面顯示「列印商品明細」按鈕**；店員視客人需求**手動決定**是否列印（有些客人要、有些不用）。預設不自動印。
  - 可**重複列印/補印**：同一筆交易可再次列印明細（補印須留稽核）。
  - 與電子發票證明聯、收據各自獨立：用載具不印證明聯時，仍可由店員選擇印明細聯。
  - （選用）`print_detail_with_sale` 設為 true 時才改為結帳隨單自動印；預設 false＝交給店員手動決定。
- **發票**：依 `settings.einvoice_enabled` 決定是否開立電子發票（見 H）。不論開關狀態，`sale` 一律完整寫入。
- 賣出序號品 → 該 `serialized_item.status = SOLD`；若為寄售 → 觸發 E 的結算。賣出散裝 → 扣該堆 `remaining_qty`，歸零轉 `SOLD_OUT`。
- 退換貨（見 I）。
- 可選會員歸戶（累點、消費紀錄）；**本期僅累點與紀錄，點數折抵規則未定、不做折抵**（預留）。
- 寫 `stock_movement`(OUT)。

### H. E-Invoice / 電子發票（Turnkey 整合）
- 目標規格：**MIG 4.0/4.1**，透過 **Turnkey v3.2** 上傳至財政部電子發票整合服務平台。
- 整合方式：系統產生 **MIG 格式 XML**，寫入 Turnkey 設定的交換/拋出目錄（如存證目錄、B2B 交換目錄、發票配號訊息目錄），由 Turnkey 上傳；並讀取 `ProcessResult`/`SummaryResult` 確認上傳結果、防止漏傳。
- 需支援：B2C 證明聯、B2B（買方統編）、作廢（void）、折讓（allowance）、字軌配號管理。
- **雲端載具（台灣消費者習慣，必做）**：結帳時店員可用條碼槍掃消費者的**手機條碼載具**（Code 39、8 碼、首碼 `/`、CarrierType `3J0002`），亦支援自然人憑證載具（`CQ0001`）、捐贈碼（3-7 碼數字）、會員/店家載具。
  - 掃到後驗證格式、存入 `invoice.carrier_type/carrier_id`（載具非 PII，明文儲存），並寫入 MIG XML。
  - 用載具時 `print_mark=N`、預設**不印證明聯**（發票存雲端），仍印一般收據；可設定。
  - 會員可選擇把常用載具存進個人檔（`contact.default_carrier_*`，需同意），下次免掃自動帶入。
- **離線韌性**：產生 XML 屬於本地檔案動作，斷網不影響開立；上傳由 Turnkey 在連線恢復後處理。系統維護 `einvoice_upload_queue` 與狀態（`PENDING/UPLOADED/FAILED`）。
- **開關**：`einvoice_enabled=false` 時不產生 XML、不配號，`sale.invoice_status=NOT_ISSUED`；可日後補開。
- 列印電子發票證明聯（透過硬體代理；用載具時依 `print_mark` 決定是否列印）。
- ⚠️ 實作前務必下載當前版 Turnkey 使用說明書與 MIG 4.0/4.1 規格，依實際欄位與目錄設定實作；載具/捐贈/統編的欄位與代碼對照當前 MIG，不得依記憶硬寫。

### I. Returns / 退換貨（RMA）
- 退貨參照原 `sale`：退現金（現金抽屜出帳，需有開帳中的 `cash_session`）、序號品回 `IN_STOCK`、數量品回補、散裝回補該堆 `remaining_qty`。
- 若退的是已售出**寄售品** → 同步反轉其 `consignment_settlement`：未付款則 `CANCELLED`；已付款則標記為「應向寄售人收回」待處理。
- 若原銷售已開發票 → 產生**折讓單（allowance）**經 Turnkey 上傳，不得直接刪發票。
- 換貨視為退＋售。

### J. Cash Drawer / 收銀對帳
- `cash_session`：開帳（零用金 float）→ 期間多筆現金異動（`SALE_IN`、`BUYOUT_OUT`、`CONSIGNMENT_PAYOUT_OUT`、`MANUAL_ADJUST`）→ 結帳（實點金額、系統應有金額、差異）。
- **規則**：所有影響現金的操作（POS 收現、收購/散裝付現、寄售付款、退貨退現）都必須在一個**開帳中的 `cash_session`** 下進行；若無開帳，前端提示先開帳。
- 每班/每日對帳；差異需記錄與稽核。

### K. Stocktake / 盤點
- 建立盤點單，掃描/輸入實際數，與系統帳比對產生差異，確認後寫調整 `stock_movement`(ADJUST) 並留痕。
- 序號品逐件確認在庫。

### L. Reporting / 財務報表分析
- 每日現金對帳報表（對應 J）。
- 營收 / 銷貨成本 / 毛利：
  - 買斷品：成本 = `acquisition_cost`。
  - 寄售品：店家收入只認 `commission_amount`（非全額售價）。
  - 散裝（E）：每件成本 = 整堆 `acquisition_cost ÷ total_qty`；可看各堆售出進度與毛利。
  - 數量品：成本來自採購。
- 庫存價值與庫齡（intake_date 起算）。
- 寄售應付未付、已實現抽成。
- 銷售趨勢、分類別/品項別毛利。
- 匯出（CSV/Excel）給會計。

### M. Audit Log / 稽核（跨模組）
- 記錄敏感操作：作廢、折讓、改價、現金調整、PII 解密查看、權限/設定變更。
- 不可竄改（append-only）。

### N. Hardware Agent / 硬體代理（跨模組能力）
- 獨立 Python 服務於 POS 機器，localhost HTTP 提供：列印收據、列印電子發票證明聯、列印序號品條碼標籤、開啟錢櫃（透過印表機 kick 指令，ESC/POS）。
- 條碼槍通常為鍵盤模擬（HID），前端直接接收輸入，不需經代理。

#### N-1. 裝置狀態檢視（Device Status，Phase 3 納入）
店員需能一眼看出櫃檯各機器是否正常，避免「印不出來才發現離線」。涵蓋機型：**Brother QL-810W**（Wi-Fi 標籤機）、**EPSON TM-T82iii**（熱感應收據/發票機，錢櫃接於其 drawer port）、**掃碼槍**、**錢櫃**。

- **A 級（保證做到）**：每台顯示「連線/離線」與「最後回應時間」（心跳）。Wi-Fi 連線的 Brother QL-810W 尤其必須有離線偵測（網路斷線常見）。掃碼槍與錢櫃因無獨立網路狀態，連線性以其所依附之主機/印表機是否在線推定。
- **B 級（能報就報、優雅降級）**：缺紙、上蓋開啟、印表機錯誤、錢櫃開啟狀態等細部狀態，**依各機型 SDK 實際支援度**顯示；SDK 查不到的項目顯示「此機型不支援」，**不可假裝有此能力、也不可當成故障**。
- **架構**：機器接在 hardware-agent 那台主機；由 hardware-agent 提供「裝置狀態查詢端點」，前端**定時輪詢**顯示成面板，前端不直接碰硬體（見 02/10 的回報介面與 04 的端點）。
- **實作前置閘門（強制，比照電子發票 MIG/Turnkey 規格之要求）**：實作 B 級前，**必須先下載 Brother QL-810W 與 EPSON TM-T82iii 的官方 Python SDK 文件**，依「SDK 實際提供哪些狀態查詢 API、回傳哪些欄位」決定每台 A/B 各能報什麼，**不得憑記憶假設機器有某功能**。此查證清單為 Phase 3 動工前置（見 07）。

### O. Notification / 通知（**預留接口，本期不實作**）
- 定義 `NotificationService` 介面（例：寄售品售出通知寄售人領款），先以 no-op/log 實作，未來接 LINE/簡訊。

### P. Settings / 系統設定
- 採**單列、具型別**的設定（每店一列、Pydantic 驗證），非 stringly-typed key-value。
- `einvoice_enabled`、`default_commission_pct`（預設寄售抽成 50，整數百分數）、`default_margin_pct`（定價輔助目標毛利率，整數百分數，預設 45）、`tax_rate`（預設 5%）與稅務處理、成色分級列舉、reorder 預設、店家基本/發票資訊（統編、字軌）等。

---

## 非功能性需求（NFR）

- **資料一致性**：收購、銷售、寄售結算、現金異動皆須在交易（transaction）內完成；金額用 Decimal、新台幣整數元（含稅定價、ROUND_HALF_UP），禁 float。
- **可用性**：POS 與收購在店內區網可離線於外網運作（只有 Turnkey 上傳需外網，採佇列）。
- **安全**：PII 欄位加密、RBAC、稽核、密碼雜湊；金鑰不入 repo。
- **可維護性**：模組化單體、清楚分層、型別檢查、Alembic migration。
- **可備份**：每晚自動 `pg_dump` + 異地/雲端複製；提供還原程序文件。
- **可擴張**：`store_id` 全面就緒；模組邊界乾淨，未來可上雲或拆服務而非重寫。
- **可觀測**：結構化 log（不含 PII）、關鍵操作可追蹤。
- **效能**：單店量級（每月數千筆），無特殊調校需求；常用查詢建索引即可。