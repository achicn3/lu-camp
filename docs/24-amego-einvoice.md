# docs/24 — 光貿 Amego 電子發票串接（取代自建 Turnkey 路線）

> 裁示（2026-07-09）：電子發票直接串光貿 Amego API（B2B＋B2C 都要），取代 T13/T14 自建
> Turnkey/MIG XSD 路線。開立後可用測試帳密登入後台查驗。POS 結帳完成若啟用發票即自動開立，
> 無載具且不捐贈時由 EPSON 直接印出電子發票證明聯。

## 1. Amego API 規格摘要（api_doc 2026-06-10 版，MIG 4.0）

- **API 網址**：`https://invoice-api.amego.tw`（測試/正式同一網址，以統編＋App Key 區分）。
- **測試憑證**：統編 `12345678`、App Key `sHeq7t8G1wiQvhAuIM27`；
  測試後台 `https://invoice.amego.tw/`（test@amego.tw / 12345678）。正式憑證洽客服，**不入 repo**。
- **傳輸**：POST `application/x-www-form-urlencoded`（勿用 JSON body），欄位：
  - `invoice`＝賣方統編
  - `data`＝API 參數 JSON 字串（**須 url-encode**；伺服器會先 url-decode 一次）
  - `time`＝Unix 時間戳（與伺服器誤差 ±60 秒；可用 `/json/time` 校時）
  - `sign`＝`md5(data JSON 字串 + time + App Key)`
- **回應**：`{code, msg, ...}`；`code=0` 成功。

### 端點（MIG 4.0；舊 c/d 系列仍可用）
| 功能 | 端點 |
|---|---|
| 開立發票（自動配號） | `/json/f0401` |
| 作廢發票 | `/json/f0501` |
| 開立折讓 | `/json/g0401` |
| 作廢折讓 | `/json/g0501` |
| 發票查詢/清單/檔案/列印 | `/json/invoice_query|invoice_list|invoice_file|invoice_print` |
| 伺服器時間 | `/json/time` |

### f0401 開立（data 欄位）
- `OrderId`（唯一、≤40 字）——用本系統 `sale.id` 衍生（如 `S{store_id}-{sale_id}`）。
- `BuyerIdentifier`：買方統編；**B2C 填 `0000000000`**。`BuyerName`：B2C 可填「消費者」；
  B2B 填公司名或統編（不可 0/00/000/0000）。選填 `BuyerAddress/TelephoneNumber/EmailAddress`、`MainRemark`。
- 載具：`CarrierType`（手機條碼 `3J0002`、自然人憑證 `CQ0001`、光貿會員 `amego`）＋
  `CarrierId1`（顯碼）/`CarrierId2`（隱碼）。捐贈：`NPOBAN`＝捐贈碼。
- `ProductItem[]`（≤9999）：`Description`（≤256）、`Quantity`、`Unit?`（≤6）、
  `UnitPrice`（**預設含稅**；未稅需 `DetailVat=0`）、`Amount`（小計）、`Remark?`、
  `TaxType`（1 應稅/2 零稅率/3 免稅）。
- 金額欄（**含稅商品、DetailVat=1 的規則**）：
  - `SalesAmount = Round(Σ TaxType=1 的 Amount)`（含稅加總）
  - B2C（不打統編）：`TaxAmount = 0`（SalesAmount 維持含稅值）
  - B2B（打統編）：`TaxAmount = SalesAmount − Round(SalesAmount/1.05)`；
    `SalesAmount = SalesAmount − TaxAmount`
  - `TotalAmount = SalesAmount + FreeTaxSalesAmount + ZeroTaxSalesAmount + TaxAmount`
  - `TaxType`（發票層）＝1、`TaxRate`＝`"0.05"`
- 選填 `printer_type`（≥2 回 ESC/POS base64）／`printer_lang`——**本系統不用**（自家 agent 依
  附件一格式列印，見 §3）。

### f0401 成功回應
`invoice_number`（字軌 10 碼）、`invoice_time`（Unix）、`random_number`（4 碼）、
`barcode`（一維條碼**內容字串**）、`qrcode_left` / `qrcode_right`（二維條碼**內容字串**；
0 元發票回空字串）。

### f0501 作廢
`data` 為**陣列**：`[{"CancelInvoiceNumber": "AB00001111"}, ...]`；回 `code/msg`。

### g0401 開立折讓（data 為陣列，每元素一張折讓）
- `AllowanceNumber`（**自編**折讓單號，唯一、≤40 字）、`AllowanceDate`（`Ymd`）、
  `AllowanceType`（1 買方開立／2 賣方折讓證明通知單；114-01-01 起賣方應開立並依限上傳→**用 2**）。
- `BuyerIdentifier`（B2C 填 `0000000000`）、`BuyerName`、選填地址/電話/信箱。
- `ProductItem[]`：`OriginalInvoiceNumber`、`OriginalInvoiceDate`（Ymd 數字）、
  `OriginalDescription`（≤256）、`Quantity`、`UnitPrice`（**不含稅**）、`Amount`（**不含稅**）、
  `Tax`（稅金）、`TaxType`（1/2/3）。
- `TaxAmount`（稅額合計）、`TotalAmount`（**不含稅**金額合計）；回 `code/msg`。

### g0501 作廢折讓
`data` 為陣列：`[{"CancelAllowanceNumber": "..."}]`；回 `code/msg`。

## 2. 本系統整合設計

- **憑證**：賣方統編用 `stores.tax_id`；App Key 走環境變數 `AMEGO_APP_KEY`（禁入 repo/DB）。
  `einvoice_enabled` 啟用閘門由「XSD serializer 就緒」改為「AMEGO_APP_KEY 已設定」。
- **開立時機**：結帳交易一律先落 `sales`＋PENDING 發票列（既有模型，來源事實不變）；
  **commit 後**於獨立交易嘗試呼叫 Amego 開立（雲端服務可能斷線，**不可讓結帳失敗**）。
  成功→存 invoice_number/random/barcode/qr 內容、狀態 ISSUED；失敗→留 PENDING，
  由「待開發票」清單手動重試（單店足夠；不做背景排程）。
- **B2C/B2B**：POS 結帳面板啟用發票欄位——買方統編（選填＝B2B）、手機載具條碼（`/` 開頭 8 碼，
  `3J0002`）、捐贈碼。含稅品項直接映射 `ProductItem`（`UnitPrice`/`Amount` 含稅、`TaxType=1`），
  金額欄依 §1 規則由後端計算（B2B 分拆稅額與本系統 `split_tax_inclusive` 同式）。
- **列印**：無載具且未捐贈 → 用 Amego 回傳的 `barcode`/`qrcode_left`/`qrcode_right` **內容**
  餵自家 agent `print_einvoice`（附件一格式一版面、既有測試管線）；證明聯條碼/QR 內容以
  Amego 回傳為準（不再本地以 AES 產生）。有載具或捐贈 → 不印。
- **作廢**：銷售作廢（invoice_status=ISSUED 時）→ `f0501`；**退貨開折讓**（不變量 #5）→
  `g0401`（後續階段）。
- **OrderId 冪等**：`OrderId` 不可重複＝天然防重複開立；重試沿用同 OrderId。

## 3. 分段實作

1. **A1 客戶端＋開立（本分支）**：`einvoice/amego.py`（簽章/傳輸/錯誤碼）、結帳後開立、
   POS 發票欄位（統編/載具/捐贈碼）、證明聯自動列印、待開清單＋重試。
2. **A2 作廢**：sales void → f0501（同交易更新 invoice_status=VOID 語意對齊）。
3. **A3 折讓**：returns → g0401/g0501。

測試以測試統編/App Key 對真 Amego 測試環境打通一次（手動驗證），單元/整合測試以
假 client（錄製回應）為主，不在 CI 內打外部服務。
