# 30 — 付款方式擴充：LINE Pay（Offline v4）＋台灣Pay 詳細計畫

> 狀態：**P1–P4、四輪金流對抗審、購物金＋其他付款的混合結帳及累計分次退貨皆已完成
> 並合併 `main`**。
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

POS 混合付款只支援購物金加其中一種剩餘渠道：店員輸入本次購物金，剩餘款項可選現金、
LINE Pay 或台灣Pay。未包含購物金的多外部渠道組合在結帳時拒絕；若舊資料出現此組合，
退貨時也 fail-closed，不猜測退款順序。

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
| 退款（以本店單號） | POST | `/v4/payments/orders/{orderId}/refund` |
| 退款（以交易號） | POST | `/v4/payments/{transactionId}/refund` |
| 作廢授權（未請款） | POST | `/v4/payments/{transactionId}/void` |

> ⚠️ **實測修正（2026-07-18 真沙盒）**：退款有兩條路徑——`/v4/payments/orders/{orderId}/refund`
> 吃**本店 orderId**（字串），`/v4/payments/{transactionId}/refund` 吃**交易號**（純數字）。
> 早先文件誤寫 `/orders/{transactionId}/refund`（該路徑只吃 orderId）→ 塞交易號回 `1150
> Transaction record not found`。**本店實作採 orderId 路徑**（我方主鍵、無 64-bit 失真風險）。

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
`1133`＝付款碼無效；`2101`＝參數錯；`1150`＝查無交易（退款路徑/ID 對錯）；`1165`＝已退款。
**transactionId 必當字串處理**（64-bit，JS/JSON `JSON.parse` 會失真）。⚠️ **實測血淚**：真收費
回的 `info.transactionId=2026071802368895010`，若經 JS `JSON.parse` 存成 Number 會被四捨成
`...895000`（尾數污染）→ 退款查無。**後端解析 pay/check 回應時，transactionId 必須從原始 JSON
文字以字串保留**（Python `json` 用 int 無失真、但轉出邊界勿落 JS Number；本店退款改用 orderId
路徑徹底避開此雷）。
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
   目前 POS 混合付款僅為 STORE_CREDIT＋CASH／LINE_PAY／TAIWAN_PAY 三選一。
3. **手續費＝店家成本**：`fee = round_ntd(amount × fee_pct/100)`（§6 ROUND_HALF_UP 整數元）。
   店家淨收＝amount − fee。手續費於**毛利報表**認列為成本（口徑待裁示，見 §7 決策 1）。
4. **LINE Pay fail-closed**：pay API 非 0000 → **整筆銷售不成立、回滾**（不得留下無付款的
   已完成單）；逾時 → poll check，COMPLETE 才成立，否則回滾並提示店員改用其他方式重收。
5. **退款/作廢對稱**：銷售作廢或退貨含 LINE_PAY → 呼叫 refund API，成功才反轉；
   linepay_transactions 記 refunded_amount，不可超額退。台灣Pay 退款＝店員於 App 手動退、
   系統記錄反轉（無 API）。
   - **P2d 已做**：作廢（void）＝**全額** refund（amount−refunded），標 REFUNDED。
   - **✅ 部分退款**：退貨可退單一品項／數量，也可由 UI 一鍵帶入剩餘全部品項，不必整筆
     作廢。`create_return` 對 LINE_PAY 只以**本次分配到 LINE Pay 的差額**呼叫
     `client.refund(order_id, amount)`，**累加** `refunded_amount`；
     linepay client/`refunded_amount∈[0,amount]` CHECK 已支援累退。狀態：未全退保持 COMPLETE
     （或新增 PARTIALLY_REFUNDED 供報表分辨），全退才 REFUNDED。多次部分退以累計不超過 amount 守護；
     每次 refund 冪等（平台 1165＝已退視為成功）。orderId 單一、累退，不需逐次新 order。
6. **冪等**：orderId 綁銷售冪等鍵；pay 重試以同 orderId，check 先查避免重複扣款
   （網路遺失回應時 poll check 收斂，不重扣）。

### 4.1 購物金優先的累計退款分配

設原銷售購物金總額為 `S`、本次退貨前已退商品金額為 `P`、本次退貨商品金額為 `R`：

- 本次購物金回補：`min(P + R, S) - min(P, S)`。
- 本次外部退款：`R - 本次購物金回補`，只回原本唯一的 CASH、LINE Pay 或台灣Pay。
- 例：原單購物金 300 元＋LINE Pay 700 元。第一次退 200 元 → 購物金 200 元；第二次再退
  200 元 → 購物金 100 元＋LINE Pay 100 元。此時 LINE Pay 原收 700 元，但商品只累退
  400 元，故不可能退 600 元；剩餘 600 元仍對應尚未退貨的商品。
- `return_tenders` 保存每次實際退款拆分並與退貨金額對平。台灣Pay 只有本次外部差額大於 0
  時才要求人工退款確認；LINE Pay durable refund log 也只記本次外部差額。
- 發票折讓與付款工具分離：G0401 仍以本次退貨商品 `R` 全額開折讓，不只折讓外部退款腿。

- 退款「金額優先順序」不等於資料庫「鎖順序」：落帳固定依寄售結算 → 現金班別 →
  購物金帳戶 → LINE Pay，避免退貨、作廢、混合結帳與寄售付款同時發生時形成反向鎖死。

### 4.2 支付復原：已做 vs 明列殘餘（Codex 四輪對抗審後，2026-07-18）

已做（防重扣/重退核心）：
- 收款 check-first（同 orderId、orderId 綁金額）＋前端冪等鍵持久化（localStorage＋記憶體後備、
  跨重整/重掛；購物車指紋前後端皆**排序正規化**、順序無關）。
- 退款 **durable append-only 日誌**（獨立交易提交、跨主交易回滾存活）＋**依 (store,order) 累計 SUCCEEDED
  對帳**（換鍵無法超退）；退款身分**由伺服器端內容＋退貨前累計已退量導出**（換前端鍵/換機/PENDING 標
  SUCCEEDED 後重做皆同鍵、不重退）；PENDING 可由店長於「LINE Pay 退款對帳」頁人工解決（SUCCEEDED/FAILED，寫 audit）。

**明列殘餘（裁示：單店單機不建，2026-07-18）**：**外部收款成功後、本地交易 commit 前失敗**造成的
「孤兒收款」（客人已扣款但無銷售）——Codex 第四輪 #2。完整解＝**server-side durable payment-intent
（capture 前先 commit intent、再對 intent finalize 銷售）＋孤兒補償退款**。裁示**不建**：單店單機
（本地 Postgres commit 幾乎不會失敗；常見的「回應遺失」已由同鍵 check-first 復原；無「另一台收銀機」）
下此複合事件機率極低，不值得為一台收銀機建銀行級支付 intent 層。若日後多收銀機/多店高頻，再回頭建。

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

### 購物金＋其他付款

1. 先選會員，再選「購物金＋其他付款」，輸入本次購物金金額（必須大於 0 且小於應付總額）。
2. 剩餘款項選現金、LINE Pay 或台灣Pay；LINE Pay 必須掃一次性付款碼，台灣Pay 必須勾選
   已收到正確剩餘金額，現金則依實收計算找零。
3. 結帳送出兩筆 tender，完成頁與商品明細聯顯示交易編號及兩腿實際金額。

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

**✅ P2e 驗收已通過（2026-07-18，`frontend/scripts/linepay-acceptance.mjs`）**：對真沙盒＋真
backend（帶 LINEPAY_* env）＋真 lucamp_e2e DB 單次跑通 9/9：解真 oneTimeKey → **經真 `/sales`
API 真收費 201**（sale #264、總額 270、平台真回 0000）→ payment_method=LINE_PAY、tender
fee_amount=4（270×1.5%）→ DB linepay_transactions=COMPLETE＋真 19 位交易號
`2026071802368903710` → **非現金：此銷售無 cash_movement**（不進抽屜）→ 日結現金報表可取（整合
不 break）→ **作廢真退款 200**（平台真退）→ DB=REFUNDED＋全額退。fail-closed 由 201/200（非 402）
反證平台端真的成功。**財報手續費獨立支出行／毛利 `payment_fee_total`／收款方式分列與
部分退款＝P4 已完成**；後續亦已重跑整合 smoke。

**實務註（2026-07-18 已探測沙盒頁）**：oneTimeKey **只以條碼圖（base64 PNG）呈現、無純文字**，自動化取碼需**解碼條碼**（barcode reader）或由店主手動讀條碼下數字提供。
**✅ 已解決＋已實測真收費（2026-07-18）**：以 Playwright 載入沙盒頁 → 取 `img[0]`（300×300 QR
的 base64 PNG）→ `pngjs` 解 PNG、`jsQR` 解碼得 oneTimeKey → 立即 `pay`。真沙盒跑通：
`pay 0000 Success`（扣 250 TWD、`info.transactionId` COMPLETE）→ `check COMPLETE` → `refund
（orderId 路徑）0000 Success`。**機械路徑（掃碼→簽章→真收→查→退）全綠**；財報手續費、
關帳非現金、收款方式分列、退貨／作廢反轉等整合面亦已完成並重驗。
真店面 P3 UI 以 POS 端相機/掃碼槍讀客人 My Code 得 oneTimeKey，不經此沙盒頁。

## 8. 實作波次（已完成；保留施工切分）

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

**第一階段（憑證/簽章骨架）**：沙盒 `POST /v4/payments/oneTimeKeys/pay` 以本店憑證＋HMAC
簽章：空 body→2101(packages NotEmpty)；補 packages＋假 oneTimeKey→1133(invalid OneTimeKey)。
證明憑證/簽章/body 結構正確、僅缺真付款碼。認證機制與端點路徑已對官方文件核對。

**第二階段（端到端真收費，2026-07-18）**：Playwright 載沙盒頁 → jsQR 解 `img[0]` QR 得真
oneTimeKey `380555560043157488` → 真收費全綠：
```
① PAY   /v4/payments/oneTimeKeys/pay        → 0000 Success  info.transactionId=2026071802368895010  BALANCE 250 TWD
② CHECK /v4/payments/orders/{orderId}/check → 0000 status=COMPLETE
③ REFUND /v4/payments/orders/{orderId}/refund → 0000 Success refundTransactionId=2026071802368895211
```
另實證：`/v4/payments/orders/{transactionId}/refund`（誤路徑）→ `1150`；`/v4/payments/{tx}/refund`
（正確交易號路徑）→ 找得到交易（回 `1165` 已退）；`/v4/payments/{orderId}/refund` → `2101`
（該路徑要求 Number）。JS `JSON.parse` 把 transactionId `...895010` 污染成 `...895000` 亦當場重現。
**結論：LINE Pay 掃碼→簽章→真收→查→退整條金流機械路徑已對真沙盒驗證通過。**
