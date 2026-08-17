# 36 — 手開紙本發票登記

字軌用完、Amego 故障或網路斷線時，電子發票開不出來。店家改用**向國稅局領用的紙本備用發票**
當場開給客人。這張紙目前系統完全不知道——本文件補上登記機制。

實作分支：`feat/manual-paper-invoice`。

---

## 1. 為什麼非做不可

開票與建單是兩支獨立的 API，發票失敗**不擋交易**（`pos/page.tsx` 結帳後開立、失敗留待補開），
這部分設計是對的。但發票失敗後若店員改開紙本，系統會停在以下狀態：

1. 該銷售永遠是「未開立」，帳面上像是漏開發票；
2. **字軌恢復後，任何人按下「重試開立」，平台就真的會開出一張** ——
   加上客人手上那張紙本 = 同一筆交易兩張發票。這是稅務事故，也是本功能最主要的動機；
3. 報表與對帳看不到這張紙本發票的稅額；
4. 客人回來退貨時，系統不知道要對哪張發票做處置。

---

## 2. 店主裁示（2026-08-16）

| 議題 | 裁示 |
|---|---|
| 手開的性質 | **真的有紙本備用發票，當場開給客人**（不是「先給明細、事後補開再寄」） |

---

## 3. 設計

### 3.1 不新增 `sale.invoice_status` 狀態

手開登記完成後，`sales.invoice_status` 走**既有的 `ISSUED`**。

`invoice_status` 在退貨政策、報表、POS、`/sales` 多處被逐值判斷；新增一個「也算已開立」的
狀態，等於要求每一處都補一個分支，**漏改一處就出錯**。改用「狀態照舊 + 另立來源欄位」，
下游的預設行為自動正確，只需在少數幾個出口顯式擋下。

### 3.2 `invoices.issue_channel`

| 值 | 意義 |
|---|---|
| `AMEGO`（預設） | 經加值中心開立的電子發票 |
| `MANUAL_PAPER` | 手開紙本備用發票 |

既有欄位已足夠承載紙本發票的內容：`invoice_no`、`invoice_date`、`invoice_time`、
`random_number`、`net`/`tax`/`total`、`buyer_tax_id`。

`barcode_text` / `qrcode_left` / `qrcode_right` 保持 NULL —— 前端 `lib/agent.ts` 的
`printEInvoice` 已因缺條碼內容而擋下列印，**這正是我們要的行為**（紙本已在客人手上，
不該再印一張證明聯）。此處不需改任何程式。

### 3.3 佇列列轉 `CANCELLED`

登記手開後，該發票所有 `PENDING`／`FAILED` 的 `ISSUE` 佇列列一律轉 `CANCELLED`。
**這是防重複開立的關鍵**：不做這一步，§1 的第 2 點就會發生。

> 實作時發現 `UploadStatus.CANCELLED` **早已存在**（enum、DB CHECK、既有作廢路徑都在用），
> 不需要新增值或 migration。

### 3.4 登記流程（`EInvoiceService.register_manual_invoice`）

1. 鎖 sale 再鎖佇列列——沿用既有全域鎖序 `sale → queue`（見 `issue_for_sale` 的註解，
   反序會與作廢／退貨路徑 AB-BA 死鎖）。
2. 發票必須是 `PENDING`。`ISSUED`／`VOID`／`VOID_PENDING` 一律拒（409）。
3. 號碼格式 `^[A-Z]{2}\d{8}$`（與 `InvoicePayload.invoice_number` 同一條規則）。
4. **金額不可更動**：登記時帶入的 `total` 必須等於發票既有的 `total`，否則拒。
   登記手開發票不是改金額的後門。
5. 寫入號碼／日期／時間／隨機碼（可空），`issue_channel = MANUAL_PAPER`，
   `status → ISSUED`；`sales.invoice_status → ISSUED`。
6. 佇列列轉 `CANCELLED`。
7. 寫 `audit_log`（§5 敏感操作：人工輸入稅務號碼）。

號碼重複由既有的部分唯一索引 `uq_invoices_store_invoice_no` 擋下，不需另外查。

### 3.5 出口擋下：`MANUAL_PAPER` 不可走 Amego

| 出口 | 處理 |
|---|---|
| 作廢 F0501 | 拒絕，提示依國稅局程序作廢紙本並保留收回聯 |
| 折讓 G0401 | 拒絕，同上 |
| 印證明聯 | 已因 `barcode_text` 為 NULL 被前端擋下，**不需改** |
| 退貨決策 | `InvoiceFacts` 加 `is_manual_paper`，決策回既有的 `REVIEW_REQUIRED`（轉人工） |

退貨那一項用既有 action 值，是刻意的：`returns/invoice_policy.py` 是純決策模組、
無 DB 無 I/O，加一個事實欄位就能完整單元測試，而 `REVIEW_REQUIRED` 的下游呈現已經存在。

### 3.6 權限與入口

- 端點 `POST /einvoice/sales/{sale_id}/manual-invoice`，限 **MANAGER**。
- UI 入口放 **`/sales` 交易紀錄**每一列（僅在 `invoice_status` 非 `ISSUED`／`VOID` 時出現）
  的「登記手開發票」按鈕。
  - 不另建發票佇列頁：`/sales` 本來就列出所有銷售且已有「發票狀態」欄，夠用。
  - 同時加一個「只看未開立」篩選——目前失敗的發票**離開 POS 完成畫面後就再也找不到**
    （前端沒有任何發票佇列頁面，`/sales` 也沒有補開動作），這個篩選是找回它們的唯一途徑。

---

## 4. 測試

- 登記成功後：發票 `ISSUED`、`issue_channel=MANUAL_PAPER`、佇列列 `CANCELLED`、
  `audit_log` 有紀錄。
- **重複開立的回歸測試**：登記手開後再呼叫 `issue_for_sale` → 必須冪等回原發票，
  **不得送出 F0401**（以假 transport 斷言未發生呼叫）。
- 金額不符、號碼格式錯、發票非 `PENDING`、非 MANAGER → 各自的拒絕碼。
- 作廢／折讓對 `MANUAL_PAPER` 發票 → 拒絕。
- `invoice_policy` 新增 `is_manual_paper` 的決策單元測試。
- 前端：`/sales` 的登記對話框與「只看未開立」篩選；瀏覽器 E2E（§1 強制）。

---

## 5. 已知限制

手開紙本發票的**作廢與折讓仍然是人工作業**（收回聯、國稅局程序），系統只負責登記與
擋下錯誤的自動化路徑，不代管紙本流程。退貨時系統會轉人工並提示，不會自作主張。
