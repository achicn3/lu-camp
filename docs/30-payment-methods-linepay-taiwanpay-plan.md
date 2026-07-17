# 30 — 付款方式擴充：LINE Pay（Offline v4）＋台灣Pay 詳細計畫

> 狀態：規劃中，動工前需使用者裁示「金流口徑決策」（§7）
> 規劃日期：2026-07-18
> 基線：`main` @ `893c4fb`（三波修復＋P0 hotfix 全在 main）
> 決策依據：CLAUDE.md §6 金額/§7 不變量/§8「影響資料模型的決策先問再做」

---

## 1. 範圍與已確認事項

新增兩種**非現金**付款方式到 POS 結帳：

1. **台灣Pay**（使用者裁示 2026-07-16）：店員另用台灣Pay App 收款、**免串 API**；系統只記錄
   為一種 tender、扣可設定的手續費。
2. **LINE Pay**（Offline API v4，使用者裁示 2026-07-18）：**只做「店家掃描客人 QR/條碼」**
   （oneTimeKeys/pay），不做客人掃店家。真串 API。

**憑證已備**（`/home/test/lu-camp/.env.linepay`，gitignore 保護、不入 repo）：
Channel ID 2010746859、Secret、`LINEPAY_API_BASE=https://sandbox-api-pay.line.me`。
**已實測沙盒認證通過**（回 1133 invalid OneTimeKey＝憑證/簽章/body 結構皆正確，僅缺真付款碼）。

---

## 2. LINE Pay Offline v4 API（已讀官方文件＋實測驗證，非憑記憶）

**認證**（所有請求）——已實測接受：
- `X-LINE-ChannelId`: Channel ID
- `X-LINE-Authorization-Nonce`: UUID v4
- `X-LINE-Authorization`: `base64( HMAC-SHA256( key=ChannelSecret, msg=ChannelSecret + apiPath + requestBody + nonce ) )`
  （GET 以 queryString 取代 requestBody）
- `Content-Type: application/json`

**端點**（host = `sandbox-api-pay.line.me` / 正式 `api-pay.line.me`）：
| 用途 | Method | Path |
|---|---|---|
| 掃客人碼付款（同步授權+請款） | POST | `/v4/payments/oneTimeKeys/pay` |
| 查詢訂單狀態（逾時 poll 用） | GET | `/v4/payments/orders/{orderId}/check` |
| 退款 | POST | `/v4/payments/orders/{transactionId}/refund` |
| 作廢授權（未請款） | POST | `/v4/payments/orders/{transactionId}/void` |

**pay 請求 body**（實測 packages 必填）：
```json
{
  "amount": 100, "currency": "TWD", "orderId": "<本店唯一單號>",
  "oneTimeKey": "<掃客人 My Code 得到的一次性碼>",
  "packages": [{ "id": "pkg-1", "amount": 100, "name": "露營用品",
    "products": [{ "name": "商品名", "quantity": 1, "price": 100 }] }]
}
```
**回應**：`returnCode "0000"`＝成功（含 `info.transactionId` 19 位長整數、`info.orderId`）；
`1133`＝付款碼無效；`2101`＝參數錯。**transactionId 必當字串處理**（64-bit，JS/JSON 會失真）。
**check 狀態**：`COMPLETE`/`FAIL`/`CANCEL`/`AUTH_READY`。逾時建議 20 秒後 poll check。

---

## 3. 資料模型（新增，需 migration）

- **enum 擴充** `shared/enums.py`：`TenderType` ＋ `LINE_PAY`、`TAIWAN_PAY`（現有 CASH/
  STORE_CREDIT）；`PaymentMethod` summary 對應擴充（single 值＋MIXED 已存在）。
- **`sale_tenders`**（已有 store_id/sale_id/tender_type/amount/unique(sale_id,tender_type)）：
  新增 `fee_amount`（Numeric(12,0)，該 tender 的手續費，整數元；現金/購物金為 0）。
  `amount`＝客人實付全額（含被扣手續費前）；`fee_amount`＝店家成本，另記、不減 amount。
- **新表 `linepay_transactions`**（比照 einvoice_upload 模式，供對帳/退款/稽核）：
  `id / store_id / sale_id / order_id(唯一) / transaction_id(String, 64-bit) / status
  (PENDING/COMPLETE/FAILED/REFUNDED/VOIDED) / amount / refunded_amount / raw_response(JSONB)
  / created_at`。退款以 transaction_id 呼叫、累計 refunded_amount 不超過 amount。
- **`settings`** 新增：`linepay_enabled`(bool)、`linepay_fee_pct`、`taiwanpay_fee_pct`
  （整數百分比或 4dp 小數，比照 tax_rate/commission 邊界驗證；預設 0）。

migration 加法、附 down；enum CHECK 擴充比照既有慣例（如 signing KIOSK migration）。

---

## 4. 金流不變量（§7，必須以測試守護）

1. **非現金、不進抽屜**：LINE_PAY/TAIWAN_PAY tender **不寫 cash_session、不影響關帳
   「應有現金」**（比照 STORE_CREDIT）。關帳報表**另列**每種方式收款額與手續費，不計入
   現金對帳。→ 現有 `cashdrawer expected` 公式不變（invariant #4 不倒退）。
2. **收款守恆不變**：`Σ sale_tenders.amount == sale.total`（客人實付全額；手續費不參與此等式）。
   混合付款可含 CASH＋LINE_PAY＋STORE_CREDIT 等。
3. **手續費＝店家成本**：`fee = round_ntd(amount × fee_pct/100)`（§6 ROUND_HALF_UP 整數元）。
   店家淨收＝amount − fee。手續費於**毛利報表**認列為成本（口徑待裁示，見 §7 決策 1）。
4. **LINE Pay fail-closed**：pay API 非 0000 → **整筆銷售不成立、回滾**（不得留下無付款的
   已完成單）；逾時 → poll check，COMPLETE 才成立，否則回滾並提示店員改用其他方式重收。
5. **退款/作廢對稱**：銷售作廢或退貨含 LINE_PAY → 呼叫 refund API，成功才反轉；
   linepay_transactions 記 refunded_amount，不可超額退。台灣Pay 退款＝店員於 App 手動退、
   系統記錄反轉（無 API）。
6. **冪等**：orderId 綁銷售冪等鍵；pay 重試以同 orderId，check 先查避免重複扣款
   （網路遺失回應時 poll check 收斂，不重扣）。

---

## 5. 結帳流程（POS）

### LINE Pay（店家掃客人碼）
1. 店員選「LINE Pay」付款 → 掃客人 LINE App「我的條碼」→ 得 oneTimeKey。
2. `POST /sales`（tenders 含 LINE_PAY）→ 後端建單 savepoint 內呼叫 pay API
   （orderId＝本單、packages 由購物車組、oneTimeKey）。
3. 0000 → 記 linepay_transaction(COMPLETE)、銷售成立、印明細/開發票（沿既有流程）。
4. 非 0000／逾時 poll 非 COMPLETE → **回滾整筆**、回錯誤，店員改現金或重掃。

### 台灣Pay（無 API）
1. 店員於台灣Pay App 收款完成 → POS 選「台灣Pay」tender、輸入/確認金額 → 建單記錄。
   （無外部呼叫；手續費由 settings 計入 fee_amount。）

### 手續費呈現
- 客人一律付全額 total（手續費不加價給客人）；手續費是店家與金流商之間的成本。
- POS 可顯示「本筆手續費 $X（店家負擔）」供店員知悉，非向客人收取。

---

## 6. 報表與對帳

- **關帳對帳**：現金 expected 不含 LINE_PAY/TAIWAN_PAY；報表另列各方式收款額＋手續費合計。
- **毛利報表**：手續費為店家成本，依裁示口徑（§7 決策 1）計入。
- **趨勢/日結**：付款方式組成（現金/購物金/LINE Pay/台灣Pay）分列。

---

## 7. 金流口徑決策（使用者裁示 2026-07-18）

1. **手續費記帳口徑**：✅ **獨立「支付手續費支出」行**——認列營收不變（客人付多少認多少），
   手續費另列為支出、毛利另扣。報表新增 payment_fee 支出彙總（依 tender 分列 LINE/台灣Pay）。
   `margin_breakdown` 加 `payment_fee_total` 欄，gross_margin 保持不含手續費、另提供
   `net_margin = gross_margin − payment_fee_total`（或報表層呈現，不動既有 gross 定義）。
2. **台灣Pay 對帳**：✅ **非現金、不計入抽屜**（比照 STORE_CREDIT/LINE Pay）。關帳「應有現金」
   不含，另列各方式收款。
3. **LINE Pay 失敗**：✅ **fail-closed**——拒付/授權失敗整筆銷售不成立、回滾；店員手動改現金
   或其他方式重收。不做自動轉現金。
4. **手續費率預設值**：settings 可設；預設先填 0（上線前由店主填實際費率），邊界比照 tax_rate。

---

## 8.5 LINE Pay 驗收準則（使用者指示 2026-07-18，P2/P3 必做）

P2/P3 LINE Pay 完成後，**必須實跑沙盒端到端真收費**，不得只單元測試：
1. 抓 `https://sandbox-web-pay.line.me/web/sandbox/payment/oneTimeKey?countryCode=TW`
   取得真實 oneTimeKey（模擬客人 LINE App「我的條碼」；每次新產、單次使用、會過期）。
2. 以本店憑證+HMAC 呼叫 `POST /v4/payments/oneTimeKeys/pay` **真的收一筆費**（returnCode 0000）。
3. 驗 `GET check` 狀態 COMPLETE；跑一筆 `refund` 驗退款；驗 linepay_transactions 記錄正確。
4. **確認所有相關整合皆無問題**：財務報表（手續費獨立支出行 payment_fee_total、毛利淨額）、
   關帳對帳（LINE Pay 非現金另列、不計入應有現金）、趨勢/日結付款方式分列、退貨/作廢反轉
   呼叫 refund、憑證聯/發票流程不受影響。
5. 全程附證據（沙盒回應、報表數字交叉驗證）。

**實務註（2026-07-18 已探測沙盒頁）**：oneTimeKey **只以條碼圖（base64 PNG）呈現、無純文字**，自動化取碼需**解碼條碼**（barcode reader）或由店主手動讀條碼下數字提供。P2/P3 測試前先解決取碼方式。

## 8. 實作波次（裁示後）

- **P1 台灣Pay**（小、無 API）：enum＋sale_tenders.fee＋settings 費率＋POS tender＋非現金對帳
  ＋報表分列。先落地建立「非現金 tender＋手續費」骨架。
- **P2 LINE Pay 後端**：linepay client（HMAC 簽章、pay/check/refund/void）＋
  linepay_transactions＋create_sale 整合（fail-closed、poll、冪等）＋退款/作廢反轉＋migration。
- **P3 LINE Pay POS UI**：掃碼輸入 oneTimeKey＋結帳流程＋失敗提示＋手續費顯示。
- **P4 報表/對帳**：關帳另列、毛利手續費口徑、趨勢分列。
- 每波 TDD＋四道門＋Codex adversarial（涉金流走 adversarial-review）＋瀏覽器 e2e＋停下確認。
  沙盒 oneTimeKey 由沙盒網頁產生對測。

---

## 附：驗證紀錄（2026-07-18）
沙盒 `POST /v4/payments/oneTimeKeys/pay` 以本店憑證＋HMAC 簽章：空 body→2101(packages
NotEmpty)；補 packages＋假 oneTimeKey→1133(invalid OneTimeKey)。證明憑證/簽章/body 結構
正確、僅缺真付款碼。認證機制與端點路徑已對官方文件核對。
