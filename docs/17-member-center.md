# 17 — 會員中心（Member Center，T21）規格

> 狀態：**已確認，進入實作（TDD）**。撰於 2026-06-14、同日經使用者逐項裁示定案（見 §10）。
> 本檔遵守 `CLAUDE.md`（最高優先）、`docs/03-data-model.md`、`docs/04-api-spec.md`、
> `docs/11-api-contract.md`（OpenAPI 為唯一事實來源）、`docs/16-store-credit.md`（購物金）、
> `docs/12-git-workflow.md`。**因會觸及既有 contacts / acquisition / inventory / sales /
> consignment / storecredit 多個模組，必須含完整回歸測試，且跨模組只經對方 service 介面。**

---

## 〇、一句話總結（重要）

T21 的領域核心**幾乎都已存在**：購物金帳本（SC-1～4）、收購來源歸屬（SC-2）、銷售買方
（SC-3）、寄售與分潤結算、會員點數（§0）皆已實作並合併 `main`。**T21 主要是「讀取／彙整層
（member facade）＋少數查詢方法＋一個會員編輯端點」，原則上不新增帳本/金額類資料表，不改動
結帳 API 的金流。** 這點是本規格最關鍵的盤點結論。

---

## 一、Step 0 既有程式碼盤點：現況 vs 缺口

### 1.1 contacts 領域（會員主檔）

| 項目 | 現況 | 缺口 / T21 動作 |
|---|---|---|
| 資料模型 `Contact` | `name`、`phone(index)`、`national_id_enc`、`national_id_blind_index(index)`、`roles[]`（MEMBER/SELLER/CONSIGNOR）、`member_points`、`default_carrier_*`、`source_note`；複合唯一 `(store_id, blind_index)` 去重、`(id, store_id)` 供他表複合 FK | 會員所需欄位齊全。**選配**：是否加 `member_no`（會員卡號）見 §3.1，預設**不加** |
| PII 加密 | AES-256-GCM 欄位層加密（`core/crypto.get_pii_cipher`）；`national_id_blind_index = HMAC`（`core/crypto.national_id_blind_index`）僅供精確去重；`reveal_national_id` 解密並寫 `audit_log`（明文不入稽核/log） | 沿用，不動。編輯 national_id 時須重算 blind index + 去重（見 §4） |
| Service | `create_contact`（blind-index 去重命中回既有）、`get_contact`、`lookup_by_national_id`、`search(role,q)`、`reveal_national_id`、`add_member_points`（原子 UPDATE，跨店/負值即拒） | **缺 `update_contact`**（編輯姓名/電話/載具/備註/national_id/roles）。新增之 |
| Router | `POST /contacts`、`POST /contacts/lookup`、`GET /contacts`、`GET /contacts/{id}`、`GET /contacts/{id}/national-id`（MANAGER） | **缺 `PATCH /contacts/{id}`**。新增之 |

### 1.2 acquisition 領域（商品來源歸屬）

| 項目 | 現況 | 缺口 / T21 動作 |
|---|---|---|
| `Acquisition` 單頭 | `type`（BUYOUT/CONSIGNMENT/BULK_LOT）、**`contact_id`（來源賣方/寄售人）**、`payout_method`（CASH/STORE_CREDIT/SPLIT）、`payout_cash_amount`、`payout_credit_cash_equivalent`、`idempotency_key` | 來源歸屬**已存在**：每筆收購單記錄 `contact_id` |
| 入庫實體連結 | `serialized_items.acquisition_id` / `bulk_lots.acquisition_id` → `acquisitions.id`；買斷成本 `acquisition_cost`，並有 DB 層「庫存背書」守衛（購物金負債須有等值自有庫存） | 連結已存在 |
| Service / Repo | repo 只有 `add` / `get` / `get_by_idempotency_key` / `get_codes` | **缺 `list_by_contact(store_id, contact_id)`**（列出某會員帶來的收購單）。新增之 |

### 1.3 inventory 狀態機（買斷 vs 寄售）

| 項目 | 現況 | 缺口 / T21 動作 |
|---|---|---|
| 序號品 `SerializedItem` | **`ownership_type`（OWNED 買斷 / CONSIGNMENT 寄售）**、**`consignor_id`（寄售人 → contacts）**、`acquisition_id`、`commission_pct`、`status`（IN_STOCK/SOLD/RETURNED_TO_CONSIGNOR/WRITTEN_OFF）、`listed_price`、`sold_date` | 買斷/寄售區分**已存在** |
| 散裝批 `BulkLot` | `consignor_id`、`acquisition_id`、`remaining_qty`、`status`（ON_SALE/SOLD_OUT/WRITTEN_OFF） | 同上 |
| Repo | `list_serialized(status, ownership_type, q)`、`list_bulk_lots(...)` | **缺以 `consignor_id` 過濾、及「以來源 contact 反查」**。新增 `list_serialized(..., consignor_id=...)`、`list_bulk_lots(..., consignor_id=...)`（純加參數，回歸測試保既有行為） |

### 1.4 sales 領域（消費紀錄）— T11/T12/SC-3

| 項目 | 現況 | 缺口 / T21 動作 |
|---|---|---|
| `Sale` | **`buyer_contact_id`**、`subtotal/tax/total`、`payment_method`（CASH/STORE_CREDIT/MIXED）、`awarded_points`、`status`、`invoice_status` | 買方歸戶**已存在** |
| `SaleLine` / `SaleTender` | 明細（品項/數量/單價/小計）；收款明細（CASH/STORE_CREDIT，Σ=total，DB 層對平＋與帳本雙向綁定） | 已存在 |
| Repo | `list_sales(store_id, date_from, date_to)` **不含 buyer 過濾**；`list_lines`、`list_tenders` 已有 | **缺 `list_sales_by_buyer(store_id, contact_id, ...)`**。新增之（或為 `list_sales` 加 `buyer_contact_id` 參數） |

### 1.5 store credit（購物金）— SC-1～4 **已完整**

| 項目 | 現況 | 缺口 / T21 動作 |
|---|---|---|
| 帳本 `store_credit_ledger` | **append-only** 不可變帳本（CREDIT/DEBIT/REVERSAL/ADJUSTMENT），`balance_after` 滾動、`signed_amount` 非零、來源 `(ACQUISITION/SALE/SALE_VOID/ACQUISITION_ROLLBACK/MANUAL)`；多層 DB trigger（immutable / reversal-guard / credit-economics / balance-chain＋帳戶列鎖 / cache-sync / tail-only id）；冪等唯一鍵＋fingerprint | **直接沿用，零 schema 變更** |
| 帳戶快取 `store_credit_accounts` | `balance`＋`version`，寫入序列化錨點，可隨時自帳本重算 | 沿用 |
| Service | `credit/debit/reverse/adjust`（唯一寫入路徑 `_write_entry`：鎖帳戶→冪等重查→帳本權威餘額→savepoint insert→IntegrityError→replay/409）、`get_balance`、`list_entries`、`reconcile`、`per_member_balances`、`aging_report`、`flows` | **會員中心查詢直接呼叫 `get_balance` + `list_entries`**，無新邏輯 |
| Router | `GET /contacts/{id}/store-credit`（餘額＋分頁異動）、`POST /contacts/{id}/store-credit/adjustments`（MANAGER＋Idempotency-Key＋寫稽核） | 已滿足「餘額查詢＋異動紀錄＋手動調整」需求 |
| 報表 (SC-4) | `/reports/store-credit/{liability,flows,reconciliation}`（MANAGER，CSV/XLSX） | 沿用 |

### 1.6 consignment（寄售分潤結算）

| 項目 | 現況 | 缺口 / T21 動作 |
|---|---|---|
| `ConsignmentSettlement` | 售出寄售品時建 PENDING：`serialized_item_id`、`sale_id`、`gross`、`commission_pct`、`commission_amount`、`payout_amount`、`status`（PENDING/PAID/CANCELLED）、`paid_at/paid_by`、`reclaim_needed` | 模型已存在（付款流程屬 Phase 4，本任務只**讀取**狀態） |
| Repo | 只有 `add` | **缺 `list_by_consignor(store_id, contact_id)`**（join `serialized_items.consignor_id`）。新增之 |

### 1.7 會員積分（member points）— §0 已實作

| 項目 | 現況 | 缺口 |
|---|---|---|
| 累積規則 | `floor(total/100)` 每筆結帳累積至 `contacts.member_points`（有 buyer 才給；購物金支付照給；收購不給；void 以 `sales.awarded_points` 沖回） | **沿用、不重造**。會員中心顯示 `member_points` 即可 |

### 1.8 盤點結論

- **不需要新的帳本/金額類資料表**（購物金、結算、收購、銷售皆已建模並有 DB 層守衛）。
- T21 = **(a) contacts 編輯端點 + (b) 各模組補唯讀查詢方法 + (c) 一個「會員中心」彙整 facade + (d) 前端 F4 會員中心畫面**。
- 風險面集中在「跨模組讀取彙整」與「PII 編輯」，**不碰金流原子性**（折抵/沖正/撥款皆已定案）。

---

## 二、架構與模組邊界

遵守 `router → service → repository → model`、「跨模組只經對方 service」（CLAUDE.md §2）。

- **會員 facade 落在 `contacts` 模組**（會員 = 具 `MEMBER` role 的 contact，語意一致，避免新建平行主檔）。
  新增 `MemberService`（或擴充 `ContactService`）作為**唯讀彙整協調者**，注入並呼叫：
  `StoreCreditService`、`SalesService`、`InventoryService`、`AcquisitionService`、`ConsignmentService`。
- 各被呼叫模組**在自己的 service/repository 補上缺口查詢方法**（§1 標示者），facade 不直接碰他模組資料表。
- 全部新端點**唯讀**（除既有的 `POST .../adjustments` 與新增的 `PATCH /contacts/{id}`）；不新增任何會改動金流的端點。

```
contacts (MemberService facade, read-only aggregation)
   ├─→ ContactService          會員主檔 / 點數 / PII reveal
   ├─→ StoreCreditService       餘額 + 異動（已存在）
   ├─→ SalesService             消費紀錄（+ buyer 過濾）
   ├─→ InventoryService         來源/寄售商品（+ consignor 過濾）
   ├─→ AcquisitionService       帶來的收購單（+ by-contact）
   └─→ ConsignmentService       寄售分潤結算（+ by-consignor）
```

---

## 三、關鍵設計問題：逐一給定**建議預設值**

> 規則：以下皆為「具體預設」，供確認；多數沿用既有定案（docs/16、ADR-012），標示「沿用」者
> 表示已實作、本任務不更動。

### 3.1 會員資料模型與 `member_no`（會員卡號）
- **預設：不新增 `member_no`**。以 `contacts.id` 為會員識別、姓名/電話為日常查詢鍵；`national_id`
  blind index 供精確去重。理由：避免引入第二套識別碼與其唯一性/補號/跨店規則的複雜度，YAGNI。
- 若日後需要實體會員卡號，再以**選配 nullable 欄位** `member_no`（每店唯一、部分唯一索引）追加，
  屬獨立小任務，不阻擋 T21。

### 3.2 購物金：append-only 分類帳 + 餘額策略
- **沿用（已實作，零變更）**：`store_credit_ledger` 為事實來源（INSERT only，DB trigger 拒
  UPDATE/DELETE）；每列 `balance_after` 為滾動餘額；`store_credit_accounts.balance` 為**對帳快取**
  （寫入序列化錨點，cache-sync trigger 設為 `balance_after`、可自癒漂移）。
- **餘額計算策略**：日常讀取走快取 `get_balance`；正確性由 `reconcile`（SUM(帳本)==快取==最新
  balance_after，含孤兒/全鏈驗證）背書，不一致**只回報不靜默修正**。會員中心顯示快取餘額，
  並可連結對帳報表。

### 3.3 折抵與結帳的原子綁定 / 冪等
- **沿用（SC-3 已實作）**：購物金折抵 = `sale_tenders(STORE_CREDIT)` ↔ 帳本 `DEBIT/SALE` 雙向
  綁定（DEFERRABLE constraint triggers：Σtender==total、同店/同買方/等額、void↔reversal 雙向）；
  結帳 `idempotency_key` + fingerprint 防重送重複折抵；餘額不足 → `409 InsufficientStoreCredit`
  整筆回滾。**T21 不改結帳，不引入新折抵路徑**。

### 3.4 收購 / 寄售撥款：現金 or 購物金？
- **收購買斷（BUYOUT / BULK_LOT）撥款**：**沿用 SC-2** — `CASH | STORE_CREDIT | SPLIT`。
  選購物金時實發 `= round_ntd(現金等值 × (1 + premium_rate))`，`premium_rate` 來自 settings
  （**預設 +10%**，硬夾 0–20%）。**預設業務建議**：對會員以「購物金可享溢價、現金不溢價」引導
  選購物金（二手店常見作法）；是否強制由店員當場與賣方議定，系統不強制。
- **寄售分潤撥款**：寄售結算（`consignment_settlement`）的**實際付款屬 Phase 4**；本任務**只讀取**。
  **【裁示 #2，2026-06-14】不可只顯示狀態標籤**：`overview` 與 `consignments` 端點**必須加總並
  顯示「該會員目前 PENDING 應撥金額」= Σ `payout_amount` WHERE `status = PENDING`**（= 店家目前
  尚欠該寄售方的金額），與各筆狀態（PENDING/PAID/CANCELLED）一併呈現。付款動作（現金/購物金）
  仍留待 Phase 4 定案、T21 不實作。

### 3.5 購物金到期政策
- **沿用 G3 裁示**：`expires_at` 欄位**保留、恆 NULL = 暫定永不過期**，待會計師確認（禮券/儲值
  歸類、效期與履約保證、稅務認列時點）。T21 **不實作到期入帳**；會員中心顯示「目前無到期」。

### 3.6 RBAC（誰能做什麼）
> **【裁示 #3，2026-06-14】PATCH 角色分流（已調整）**：

| 操作 | 角色 | 稽核 |
|---|---|---|
| 查會員列表/明細、購物金餘額+異動、消費/寄售/來源紀錄 | CLERK + MANAGER（store 範圍） | 否 |
| 編輯**一般欄位（姓名、備註、載具）＋電話** | **CLERK + MANAGER** | **是**（UPDATE_CONTACT，含變更欄位 before/after；電話屬聯絡資訊一律留痕） |
| 編輯 `roles` | **MANAGER only** | **是**（UPDATE_CONTACT_ROLES） |
| 編輯 / 查看 `national_id`（PII） | **MANAGER only**；查看走既有 `reveal`（解密寫稽核） | **是**（VIEW_NATIONAL_ID / 編輯 UPDATE_CONTACT_PII，before/after **僅記旗標、不含明文**） |
| 手動調整購物金 | **MANAGER only**（沿用）；必填事由 + Idempotency-Key | **是**（STORE_CREDIT_ADJUST，含前後餘額+事由） |
- **防舞弊**：購物金手動調整恆 MANAGER + 必填 reason + 冪等鍵 + audit_log（沿用 SC-1）。
- **電話＝明文（裁示 #1/#5）**：phone 維持 `String(30)` 明文 + B-tree 索引，查詢/去重走明文精確+模糊
  比對；**phone 無 blind index**（只有 `national_id` 有）。電話編輯僅更新明文值並寫稽核，
  **不涉 blind index 重算、不需 migration**。
- **MEMBER 移除守衛（裁示 #3＋Codex 對抗式審查 high）**：移除 `MEMBER` 角色前，facade 以
  `StoreCreditService.has_store_credit`（**唯讀**）確認該 contact 未持有購物金帳戶/帳本；仍持有則
  `409` 拒絕——否則會留下「非會員仍掛購物金負債」、破壞 storecredit 的會員邊界（I-8）並使報表
  錯分類。`SELLER`/`CONSIGNOR` 移除**不受此限**（其關聯為 `contact_id` 直接 FK、非角色閘）。
  跨模組只經對方 service（contacts→storecredit 唯讀；以函式內 import 打破循環相依）。
  **併發強一致（Codex 對抗式審查 high，已收緊）**：storecredit 入帳/校正的 `_require_member`
  改以 `SELECT … FOR UPDATE` 鎖定該 contact 列再驗會員資格，與本守衛在**同一列**互斥——
  關閉「移除 MEMBER ⇄ 並發首筆入帳」競態（兩者皆鎖 contact 列；鎖序固定 contact→account，
  與既有帳戶鎖無循環、不致死鎖）。以確定性兩交易測試守護
  （`test_contacts_update_concurrency.py`）。

### 3.7 跨店資料隔離（多店路線圖）
- **沿用 CLAUDE.md §4**：每表帶 `store_id`，所有查詢以 `user.store_id` 範圍過濾；複合 FK
  `(id, store_id)` 在 DB 層保證租戶配對。blind index 去重在 `(store_id, blind_index)` 範圍。
  會員中心所有彙整查詢一律 store-scoped；「總部跨店彙整」屬未來，T21 不開。

### 3.8 PII
- **沿用**：`national_id` 靜態加密 + blind index，明文不落 log/一般回應；解密查看限 MANAGER 且
  寫稽核。**編輯 national_id**：重新加密 + 重算 blind index + 命中他人既有則拒（避免重複建檔），
  且**重算去重與唯一性檢查須與寫入在同一交易內原子完成**（詳見 §4.3）。
- **phone（裁示 #1/#5）**：維持**明文** + 一般索引，不加密、不 blind index；屬聯絡資訊，
  `ContactRead` 照常回傳（與既有行為一致）。

---

## 四、資料模型與 Alembic 遷移計畫

### 4.1 結論：**預設無新表、無金額欄位變更**
購物金帳本、帳戶、收購、銷售、收款、寄售結算、點數欄位皆已存在並有 DB 守衛。

### 4.2 可能的（**選配，預設不做**）遷移
- 若採 §3.1 `member_no`：新增 `contacts.member_no String?`，部分唯一索引
  `(store_id, member_no) WHERE member_no IS NOT NULL`。**預設不做**。
- 其餘 T21 變更皆為**程式碼層**（service/repository 查詢方法、facade、router、schema），
  **不需 migration**。若最終一個 migration 都沒有，則本任務不觸及 `alembic/`、不觸發契約以外的
  四道門 migration roundtrip 風險。

### 4.3 編輯 national_id 的一致性與**原子性**（service 層，非 schema）

> **【裁示，2026-06-14】Codex 重點審查項**：blind index 重算 + 去重 + 唯一性檢查必須在
> **同一交易內原子完成**，防併發兩請求各自 pre-check 通過後雙雙寫入而繞過去重。
>
> **【Codex 對抗式審查補強】列鎖序列化（D-1 模式）**：`update_contact` 以
> `SELECT … FOR UPDATE`（`populate_existing=True`）鎖定該 contact 列後才讀-改-驗，使
> 「SELLER/CONSIGNOR ↔ national_id 必填」這個**跨欄位**不變量於持鎖期間以最新 committed
> 狀態重驗。否則「A 交易清 national_id、B 交易加 SELLER」會各以舊快照通過檢查、最終寫出
> 「SELLER 卻無 national_id」的壞列。空白/全空白 national_id 一律正規化為「無」（同 create
> 的 falsy 處理），不可用空字串偽裝 `has_national_id` 繞過此不變量。

- 流程（單一交易內）：解析新 `national_id` → 算 `blind_index` →
  - 服務層先查同店他人（≠ 自己）既有 blind index：命中 → `409`（重複建檔；編輯時拒而非回既有）。
  - 否則設 `national_id_enc = encrypt(new)`、`national_id_blind_index = new_blind`，flush。
- **交易邊界與失敗回滾**：服務層的 pre-check **不是**最終防線——最終由 DB 既有複合唯一約束
  `uq_contacts_store_blind_index (store_id, national_id_blind_index)` 保證。並發競態下輸家會撞
  `IntegrityError`，**整筆交易回滾**並轉成 `409`（不可吞例外、不可留半套狀態）。router 在 `commit`
  前後分別處理 `409`（service `DuplicateContact`）與 `IntegrityError`（catch → rollback → 409）。
- 清空 national_id：兩欄位設 NULL（唯一約束允許多筆 NULL）。
- 寫 `audit_log`（action=`UPDATE_CONTACT_PII`，before/after **僅記「有/無、是否變更」旗標，不含明文**）。

---

## 五、API 介面草案

> 全部掛 `contacts` 模組 router，`operation_id` 駝峰、完整 `response_model`（契約優先，
> `docs/11`）；前端用 OpenAPI 生成 client。store 範圍由登入者 `store_id` 決定。

### 5.1 既有（沿用，不改）
- `POST /contacts` `createContact`
- `POST /contacts/lookup` `lookupContact`（national_id 放 body）
- `GET /contacts` `listContacts`（`role`/`q`/分頁）
- `GET /contacts/{id}` `getContact`
- `GET /contacts/{id}/national-id` `revealContactNationalId`（MANAGER）
- `GET /contacts/{id}/store-credit` `getStoreCredit`（餘額 + 分頁異動）
- `POST /contacts/{id}/store-credit/adjustments` `adjustStoreCredit`（MANAGER + Idempotency-Key）

### 5.2 新增
| 方法 | 路徑 | operation_id | 角色 | 說明 |
|---|---|---|---|---|
| PATCH | `/contacts/{id}` | `updateContact` | CLERK；含 `national_id`/`roles` 變更時 **MANAGER** | 編輯會員；PII 變更走 §4，寫稽核 |
| GET | `/contacts/{id}/overview` | `getMemberOverview` | CLERK+ | 會員中心彙整：profile 摘要 + 點數 + 購物金餘額 + **PENDING 寄售應撥加總** + 各區計數（近期摘要，皆**非全史**） |
| GET | `/contacts/{id}/purchases` | `listMemberPurchases` | CLERK+ | 消費紀錄（sales by buyer；金額/日期/品項數/付款方式/狀態），**分頁**＋日期區間 |
| GET | `/contacts/{id}/purchases/{saleId}` | `getMemberPurchaseDetail` | CLERK+ | 單筆銷售明細（lines + tenders） |
| GET | `/contacts/{id}/consignments` | `listMemberConsignments` | CLERK+ | 寄售商品 + 結算（在庫/已售/已結算；`commission_pct`/`payout_amount`/status），**分頁**；回應另含 **`pending_payout_total` = Σ payout_amount WHERE PENDING**（裁示 #2） |
| GET | `/contacts/{id}/sourced-items` | `listMemberSourcedItems` | CLERK+ | 此會員「帶來」的商品**單一合併清單**，**分頁** |

> **`PATCH` 角色分流（裁示 #3）**：router 先以 CLERK 准入；payload 若含 `national_id` 變更 →
> service 要求 MANAGER（否則 `403`）；含 `roles` 變更 → 同樣要求 MANAGER。姓名/備註/載具/**電話**
> CLERK 可改（電話為明文，僅更新值 + 寫稽核，不涉 blind index）。

> **`sourced-items` 必須 union 兩條來源（裁示，Codex 重點審查）**：
> (1) **買斷**：`acquisitions.contact_id == 會員` → 經 `acquisition_id` 連到 `serialized_items`
>     (`ownership_type=OWNED`) 與 `bulk_lots`；
> (2) **寄售**：`serialized_items.consignor_id == 會員`、`bulk_lots.consignor_id == 會員`。
> **合併為單一清單、每列標明 `source_type`（BUYOUT / CONSIGNMENT）與 `kind`（SERIALIZED / BULK_LOT），
> 不可二選一**。可選 `source_type` / `status` 過濾。store 範圍過濾。

> **彙整端點一律分頁、嚴禁 eager load 全史（裁示）**：`overview` 僅取「計數 + 近期 N 筆摘要 +
> 加總值（餘額、PENDING 應撥）」，各清單端點 `limit/offset` 分頁；不得在單一請求拉取會員全部
> 消費/寄售/來源歷史。加總值（購物金餘額、PENDING 應撥）以 SQL 聚合計算，不在應用層載全部列再加總。
>
> **跨來源合併分頁語意（裁示 2026-06-15，務實 best-effort；Codex review）**：`sourced-items`
> 與 `consignments` 合併「序號品 + 散裝」兩異質來源；因模組邊界不可跨表 `UNION`，採
> 「各來源各取至 `offset+limit` → 合併 → 切片」。全序固定為 **`(intake_date desc, 來源序, id desc)`**，
> 與各來源的 `id desc` 取列一致、且確定性可重現。**已知界線**：當大量列共用**同一 `intake_date`**
> （如同交易建立、`now()` 交易內恆定）且恰跨分頁 `cap` 邊界時，邊界該頁的成員為 best-effort
> （順序仍確定）。一般情況（`intake_date` 隨 `id` 單調遞增）分頁正確。若日後需嚴格精確分頁，
> 再改為 per-source/per-kind 各自 SQL 分頁（API 分區回應）。

### 5.3 Schema（重點欄位；皆遮罩 PII）
- `ContactUpdate`：所有欄位 optional（PATCH 語意）；`national_id` 變更走 §4；`roles` 限既有 enum。
- `MemberOverviewRead`：`contact: ContactRead`、`member_points`、`store_credit_balance`、
  **`pending_consignment_payout`（Σ PENDING `payout_amount`；裁示 #2）**、
  `counts {purchases, consigned_active, consigned_settled, sourced_items}`、`recent_purchases[]`（精簡、上限 N 筆）。
- `MemberPurchaseRead`：`sale_id`、`created_at`、`total`、`payment_method`、`status`、`line_count`。
- `MemberConsignmentsRead`（清單回應）：`items: MemberConsignmentRead[]`、
  **`pending_payout_total`（Σ PENDING `payout_amount`）**、分頁資訊。
  - `MemberConsignmentRead`：`item_code`/`name`、`item_status`、`gross?`、`commission_pct`、
    `commission_amount?`、`payout_amount?`、`settlement_status?`、`sold_date?`。
- `MemberSourcedItemRead`：**`source_type`（BUYOUT/CONSIGNMENT）**、`kind`（SERIALIZED/BULK_LOT）、
  `code`、`name`、`status`、`acquisition_id?`、`intake_date`、`listed_price`。
  **不回收購成本（`acquisition_cost`）給 CLERK**（成本敏感，比照既有 inventory 唯讀已排除成本）。

---

## 六、狀態機

- **購物金**：沿用既有（CREDIT/DEBIT/REVERSAL/ADJUSTMENT；帳本不可變）。**無新狀態機**。
- **寄售結算**：沿用 `PENDING → PAID / CANCELLED(+reclaim_needed)`（付款屬 Phase 4）。T21 只讀。
- **序號品/散裝批**：沿用既有狀態機，只讀顯示。
- **結論：T21 不引入新狀態機。**

---

## 七、測試計畫（TDD；覆蓋率門檻 services/domain ≥90%、整體 ≥80%）

### 7.1 單元（service / domain）
- `ContactService.update_contact`：一般欄位更新；**電話更新（明文）+ 寫稽核、不涉 blind index**；
  national_id 變更重算 blind index；與他人 blind index 撞 → 409；清空 national_id → NULL；
  national_id/roles 變更需 MANAGER；audit 不含明文。
- `MemberService` 彙整：正確聚合各 service 回傳；空資料（無消費/無寄售/無購物金）回空集合不報錯；
  跨店請求（contact 屬他店）→ 視為查無 / 404；點數與購物金餘額取值正確；
  **`pending_consignment_payout` = Σ PENDING payout_amount 正確**（含 PAID/CANCELLED 不計、無寄售為 0）。
- **`sourced-items` union**：同時有買斷與寄售來源的會員 → 單一清單含兩類、`source_type` 標示正確；
  只有其一者亦正確；序號品與散裝批皆涵蓋；**成本欄不外洩**。
- 各模組新查詢：`SalesRepository.list_sales_by_buyer`、`AcquisitionRepository.list_by_contact`、
  `InventoryRepository.list_serialized/bulk_lots(consignor_id=...)`、
  `ConsignmentRepository.list_by_consignor` + `pending_payout_total_by_consignor`（SQL 聚合）—
  含 store 範圍過濾、分頁、排序。

### 7.1b 原子性 / 併發（Codex 重點審查）
- **national_id 編輯去重原子性**：模擬兩請求同時把不同會員改成**同一** national_id →
  一成一敗（敗者 `IntegrityError` → rollback → 409）；證明 service pre-check 之外 DB 唯一約束為最終
  防線，且失敗整筆回滾、不留半套狀態。
- **彙整端點不 eager load**：以「會員具大量歷史」測資，驗證各清單端點回傳受 `limit` 限制、
  `overview` 不回全史、加總值以聚合計算（不退化為載全部列）。

### 7.2 整合（API；含 RBAC 與隔離）
- `PATCH /contacts/{id}`：CLERK 可改電話/備註；CLERK 改 national_id/roles → 403；MANAGER 可改 → 寫稽核。
- 各 GET 端點：store 隔離（他店 contact 404 / 空）、分頁、日期過濾、PII 遮罩、成本不外洩。
- 會員中心 `overview`：與底層各端點數字一致（交叉驗證）。

### 7.3 回歸（強制）
- 既有 contacts / storecredit / sales / acquisition / consignment / inventory 測試全綠
  （新增的是**加參數**與**新方法**，不得改變既有呼叫行為）。
- 購物金折抵/沖正/收購撥款的既有 DB 守衛測試不受影響（本任務不改金流）。

### 7.4 原子性 / 併發
- 本任務新端點**唯讀**，無新寫入原子性風險；**購物金折抵併發/冪等沿用 SC-3 既有測試**，
  不重寫。`update_contact` 的 blind-index 唯一性以既有 `(store_id, blind_index)` 約束守，
  測試並發改成同一 national_id → 一成一敗（409）。

---

## 八、與 T19 / 既有 sales API 的相依與影響面

- **T19（POS 結帳前端）**：購物金折抵在結帳的整合（SC-3）**已完成**，T19 直接使用既有
  `POST /sales` 的 `tenders`。**T21 不改結帳 API、不改金流**，因此 **T21 與 T19 無金流相依**。
- **前端關係**：T21 前端 = **F4 會員中心**，建立於 T19 的 F1 基礎（auth/版面/生成式 client）之上。
  後端可**現在即獨立進行**（SC-1～4 已合併）；前端 F4 待 F1 就緒後接續，可與 T19 其餘畫面並行。
- **對既有 sales API 影響**：僅**新增** `list_sales_by_buyer` 查詢路徑（或為既有 repo 方法加
  `buyer_contact_id` 參數，預設 None 保持相容）；**不更動** `POST /sales`、收款、沖正。
- **契約漂移**：新增端點 → 須更新 OpenAPI 生成物（四道門之一），合併前比對最新。

---

## 九、任務切分與交付（實作階段，待確認後執行）

> 每子項一分支（`feat/...`）、TDD、Codex 兩輪對抗式審查（涉 PII/權限者視為高風險，
> 用 `/codex:adversarial-review`）、四道門全綠 + rebase 後 ff-only 合併、合併後刪分支、push。
> 無相依、不碰共用檔者可並行；動到同一模組者序列。

1. **T21-a**：`contacts` 編輯端點（`PATCH /contacts/{id}` + `ContactUpdate` + service + PII/RBAC/audit）。**高風險（PII）**。
2. **T21-b**：各模組唯讀查詢方法（sales by-buyer、acquisition by-contact、inventory by-consignor、consignment by-consignor）。可並行（不同模組、不碰共用檔）。
3. **T21-c**：`MemberService` facade + 彙整端點（overview/purchases/consignments/sourced-items）。相依 T21-b。
4. **T21-d**：前端 F4 會員中心（Next.js，zh-TW，OpenAPI client；用 `ui-ux-pro-max` skill）。相依 T21-a/c 的契約。

---

## 十、決策裁示（2026-06-14，使用者逐項定案）

1. **不新增 `member_no`**：無實體會員卡；會員查詢走電話（**明文**索引）。**連帶確認無 migration**（§3.1）。
2. **寄售付款留 Phase 4、T21 只讀**——但 `overview` / `consignments` **必須加總並顯示「該會員
   PENDING 應撥金額」**（我欠寄售方多少），不可只有狀態標籤（§3.4/§5）。
3. **PATCH 角色分流（調整）**：一般欄位 + **電話** → CLERK（電話明文，更新值 + 稽核）；
   `national_id` + `roles` → **MANAGER + 稽核**（§3.6/§5.2）。
4. **CLERK 不可見收購成本**（sourced-items 不回 `acquisition_cost`；§5.3）。
5. **無 migration**（承 #1；phone 維持明文，不加 blind index）（§4）。

### 實作要求（已寫入本規格，Codex 重點審查）
- **national_id 編輯**：blind index 重算 + 去重 + 唯一性檢查**同一交易內原子完成**，防併發繞過去重；
  交易邊界與失敗回滾見 §4.3。
- **sourced-items**：必須 **union 買斷（`acquisition.contact_id`）與寄售（`consignor_id`）兩條來源**，
  合併為單一清單並標明類型，不可二選一（§5.2）。
- **彙整端點**：清單一律**分頁**，**勿 eager load 會員全史**；加總值以 SQL 聚合（§5.2/§7.1b）。

### 分支衛生（裁示）
- 從 **`feat/member-center-*`** 開乾淨分支（自最新 `main`），TDD。
- **`docs/17` 落在新分支**，**勿混入 `feat/reports-store-credit` 的 SC-4 變更**。

> 以上已定案，依 §9 開始 T21-a（`feat/member-center-contact-update`，TDD）。
</content>
</invoke>
