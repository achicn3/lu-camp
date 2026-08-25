# 32 — 贈品與臨時折扣

門市要能在結帳當下**送東西**與**臨時改價**。這兩件事看起來相近，會計性質卻完全不同：
折扣是「少收的錢」，贈品是「送出去的成本」。系統把它們分成兩條線，從資料模型一路分到報表，
**任何一層混在一起，之後就再也拆不開**。

實作於 `feat/gift-and-manual-discount`（P1–P7），店主裁示見 §1。

---

## 1. 店主裁示（2026-08-02）

| 議題 | 裁示 | 代價 |
|---|---|---|
| 整單全贈品（總額 0） | **支援** | 零元銷售的守衛必須放寬，但只對「整單都是贈品」放行 |
| 主管核准機制 | **不做** | 任何能結帳的人都能打任意折扣、送任意贈品，只能事後稽核 |
| 折扣／贈品上限 | **不設** | 同上；折扣報表的「依店員」是唯一能看出異常的地方 |
| 寄售品 | **不參與任何折扣，亦不可贈送** | 拿別人的貨做人情，寄售人會拿 0 |
| 一般商品成本 | 新增 `catalog_products.unit_cost`，**採購收貨時自動帶入最新進價**（裁示 2026-08-03） | 從未收過貨的商品沒有成本，贈品成本顯示為 0（沿用「成本未知不假造」口徑） |
| 成本快照 | 所有銷售明細存**成交當下**的成本 | 日後調整商品成本不再回頭改寫歷史毛利 |

> 原需求書有 §4 權限控制與 §9 的 `approved_by`／各項上限。依上述裁示**一律不做**。
> 這是明確的商業取捨，不是實作疏漏。

---

## 2. 第一條原則：贈品不是 100% 折扣

贈品**不是**折扣、不是負數明細、不是把售價改成 0，而是一種獨立的**明細性質**：

- 照樣扣庫存、照樣寫庫存異動（`GIFT` / `GIFT_RETURN`）
- 成交 0 元，但留下**原價**（`original_unit_price`）與**成本**（`cost_snapshot`）
- 其原價價值**絕不混入折扣金額**——活動報表直接 SUM 折扣欄位，混進去就污染了

反過來也擋著：**一般行的實付不得折到 0**。要免費請開贈品，否則贈品的數量與成本在報表上
統計不到。這條由**定價純函式與 service** 守著——**沒有 DB CHECK**（見 §9 取捨表：0 元的
一般商品本來就賣得出去，加了會把清楚的 422 變成看不懂的資料庫錯誤）。

---

## 3. 資料模型

### 3.1 `sale_lines` 擴充

`line_type` 是**品項種類**（SERIALIZED/CATALOG/BULK_LOT/MENU），把 GIFT 塞進去會混淆兩個
正交概念——「贈送一件序號品」必須表達得出來。故另立 `line_kind`。

| 欄位 | 意義 |
|---|---|
| `line_kind` | `NORMAL｜GIFT`，商業性質 |
| `line_total` | **活動折後的牌價小計**（`unit_price × qty`），語意不變 |
| `manual_discount_amount` | 本行分攤到的臨時折扣 |
| `net_amount` | **本行實付** ＝ `line_total − manual_discount_amount` |
| `cost_snapshot` | 成交當下的成本（本行合計） |
| `gift_reason_id` / `gift_reason_name` / `gift_note` | 贈品來歷（名稱快照） |

**`Σ net_amount == sale.total == Σ tenders`** 是本設計的核心等式。它成立，發票才送得出去、
退款才退得對、毛利才算得準。

> **由誰守著**：定價純函式與 service。**資料庫層沒有守 `Σ net_amount`**（裁示不做，
> 見 §9 的取捨表）；DB 只以 deferred trigger 守 `Σ tenders = sales.total`。
> 也就是說，繞過應用層的 raw DML 仍可能落下不一致的單——不要以為 DB 有這道保險。

DB CHECK 守護：

```sql
-- 贈品行的形狀
line_kind <> 'GIFT' OR (unit_price = 0 AND line_total = 0 AND net_amount = 0
  AND discount_amount = 0 AND manual_discount_amount = 0
  AND original_unit_price IS NOT NULL AND gift_reason_id IS NOT NULL)
-- 一般行的實付定義
line_kind <> 'NORMAL' OR net_amount = line_total - manual_discount_amount
```

### 3.2 折扣紀錄

- **`sale_adjustments`**：每筆折扣的意圖與來歷。`requested_value` 是店員輸入值、
  `applied_amount` 是系統實際套用金額。**報表一律用 `applied_amount`，不重算。**
  不可實刪，只能作廢。
- **`sale_adjustment_allocations`**：分攤結果。**必須落盤**——退貨要知道「這一行當初實際
  被折了多少」，不能依當下商品狀態重算（商品價格、活動、甚至商品本身都可能已經變了）。

### 3.3 原因代碼

`gift_reasons` / `discount_reasons`：`code`（同店唯一、**建立後不可改**）、`name`、
`is_active`、`requires_note`、`sort_order`。

- **停用不實刪**：歷史單據引用過的原因不能因為後台刪掉就消失。
- 單據另存 `reason_name` 快照：改名不回溯改寫歷史。
- **code 不可改**：報表以 code 對照分類，改了會讓同一件事在報表上斷成兩段。
- 新開的門市由 `app/modules/sales/reasons.py` 的 `ensure_default_reasons()` 佈建預設值——
  沒有原因代碼的門市根本送不出贈品（POS 選單會是空的）。

---

## 4. 定價與分攤

純函式：`backend/app/modules/sales/pricing.py`（無 DB、無 I/O，可完整單元測試）。

### 套用順序（固定，不可調換）

1. 各行金額 = 活動折後金額（`line_total`）
2. **單品折扣**：逐筆套到指定行
3. **整單折扣**：以「扣掉單品折扣後的**餘額**」為基礎，依比例分攤到各可折行
4. 應付金額 = Σ 各行實付

以餘額（而非原始金額）為分攤基礎是刻意的：否則已被單品折扣打到很低的行，可能分到超過它
剩餘金額的整單折扣，就得夾住再重新分配，分攤結果反而變得無法預測。

### 尾差：最大餘數法

先取各行的整數部分（無條件捨去），再把剩下的元數依「小數部分大到小」逐一發放；同分時以
原順序決定。**每筆分攤必為非負、總和精確等於折扣金額**，且可重現——否則退貨時對不上當初
的金額。

> **不可用「前 N−1 筆四捨五入、最後一筆吃差額」**：各自進位後會超發，最後一筆拿到**負數**
> 分攤，反而把該行的實付推到原價之上。實例：51、51、51、47 分攤 2 元 → 1、1、1、−1，
> 末行 47 變成 48（Codex 對抗審查 2026-08-03 high，已修並有回歸測試）。

### 排除

贈品、寄售、餐飲皆不可折（沿用活動折扣的既有排除口徑）。整單折扣分攤時跳過它們，
不讓它們吸收折扣。

---

## 5. 退貨與發票

### 5.1 退款：差額法

一行的實付已含整單折扣分攤下來的金額，除以數量往往除不盡。若每次退貨都各自
`round_ntd(實付 ÷ 數量) × 本次退量`，分次退完的加總會與原實付差幾元——少退是坑客人、
多退是店家虧損，而且差幾元永遠對不平。

```
entitlement(x) = round_ntd(net_amount × x ÷ qty)      # 全退時直接取 net_amount，不經四捨五入
本次退款       = entitlement(已退 + 本次) − entitlement(已退)
```

實作於 `backend/app/modules/returns/refund.py`。這讓**最後一件自動吸收尾差**，且累計退款
恆等於原實付、永不超過。repo 既有的散裝 COGS 與點數沖回都是這個模式。

會員點數沖回的分母與基準一併認 `net_amount`——點數當初就是按實付發的。

### 5.2 贈品退回

庫存加回、寫 `GIFT_RETURN` 異動、**退款 0**。
`ck_returns_refund_amount_positive` 已放寬為 `>= 0`（migration `c7e9a1b3d5f2`）；
零元退貨不產生任何退款渠道明細（deferred 對平守衛看的是加總，0 == 0 仍成立）。

### 5.3 主商品退了、贈品沒退

**系統不自行假設**。退貨預覽回 `unreturned_gifts`，畫面提示並鎖住送出鍵，店員必須說明
不收回的原因；原因連同贈品清單寫入 `CREATE_RETURN` 稽核。只退贈品不受此限。

### 5.4 發票

- 品項金額改讀 **`net_amount`**（因為 `Σ net_amount == sale.total`）。
  `amego.py` 硬性要求 `Σ 品項 == 發票總額`，不符就拒送、發票永遠送不出去。
- **贈品行排除於發票品項之外**：贈品實付 0，排除後 Σ 仍等於總額，
  且**不必假設平台接受 0 元品項行**（本 repo 對此無任何佐證）。
- **零元銷售不開發票**：`Invoice.total > 0` 的 CHECK 保留，`invoice_status = NOT_ISSUED`。

---

## 6. 報表公式

集中於 `margin_breakdown`（既有單一口徑，R2/R5/R6/insights/campaign-performance 五份共用）。

```
營收（各桶）    = Σ net_amount                       （NORMAL 行，排除贈品）
成本            = Σ COALESCE(cost_snapshot, 即時 join)（NORMAL 行）
臨時折扣        = Σ manual_discount_amount
贈品原價價值    = Σ original_unit_price × qty         （GIFT 行）
贈品成本        = Σ cost_snapshot                     （GIFT 行）
gross_margin    = 自有(實付 − 成本) + 寄售抽成         （**不含贈品成本**）
net_margin      = gross_margin − 支付手續費
contribution_margin = net_margin − 贈品成本
```

**規則**

- 贈品原價**不計入營業額**、**不計入折扣總額**。
- 贈品成本**獨立呈現**，不混入商品毛利——營收 0 加全額成本會讓 `gross_margin_rate` 失真。
- 成本一律取 `cost_snapshot`；NULL（舊資料或從未收過貨的一般商品）才回退即時 join，
  沿用既有的「成本未知不假造毛利」口徑。
- **一般商品的成本來源＝採購收貨時的進價**（裁示 2026-08-03，最新進價）：
  `restock_catalog_items` 於收貨時把 `catalog_products.unit_cost` 更新為該次 PO 明細的
  `unit_cost`。這是唯一的寫入路徑——沒有它，即使採購單上早有真實進價，贈品成本與
  貢獻毛利仍會系統性顯示為 0。成本只影響**日後**的成交：已成交的明細存有 `cost_snapshot`，
  進價變動不會回頭改寫歷史毛利（有回歸測試釘住）。
  留痕不另寫稽核列——`purchase_order_lines.unit_cost` 與 `goods_receipts` 本身就是
  append-only 的來源紀錄，查得到是哪一批貨把成本改成多少。
- 折扣報表用 `applied_amount`，**不事後重算**。
- 作廢的銷售不計入任何一份。
- 贈品報表保留**送出總額**，並另列按退貨發生日歸屬的**退回**與**淨額**；
  `GIFT_RETURN` 庫存異動仍供逐筆追查。貢獻毛利以淨贈品成本計算，退回時按原成交
  成本快照沖回，不用目前商品成本重算歷史。

### 端點

| 端點 | 內容 |
|---|---|
| `GET /api/v1/reports/discounts` | 依原因、**依店員**彙總（json/csv/xlsx） |
| `GET /api/v1/reports/gifts` | 依原因、依品項彙總（json/csv/xlsx） |
| `GET /api/v1/reports/sales-margin` | 既有，增 4 個指標 |

「依店員」那一段是刻意的：沒有主管核准機制（§1），異常的折扣量只能從這裡看出來。
未指定原因的折扣歸為「未指定原因」一列——不能讓它從報表消失，否則正是想藏的那些看不到。

---

## 7. API

| 端點 | 說明 |
|---|---|
| `POST /api/v1/sales`、`/sales/quote` | 請求加 `lines[].line_kind/gift_reason_id/gift_note` 與 `adjustments[]`；quote 回金額摘要 |
| `PUT /api/v1/customer-display/terminals/{id}/cart` | 同上加 `adjustments[]` |
| `GET/POST /api/v1/gift-reasons`、`PATCH /{id}` | 原因代碼（GET 任何登入者、寫入限 MANAGER） |
| `GET/POST /api/v1/discount-reasons`、`PATCH /{id}` | 同上 |

**折扣目標以「明細順序索引」指定**（`target_line_index`）：成交前 `sale_line` 還沒有 id，
而前後端共用同一份明細順序。前端則以購物車列的**穩定 key** 記錄，送出時才換算成索引——
直接存索引的話，店員移除前面一列就會讓折扣默默跑到別的商品上。

---

## 8. 客顯購物車（權威購物車）

客顯是**權威購物車**：結帳時把它的快照與實際成交明細**逐欄位、依序、byte-exact** 比對。
贈品與臨時折扣改變金額，所以：

- 快照與簽署內容加上 `line_kind` / `manual_discount_amount` / `net_amount`，
  **四個產生點**必須同步（`_cart_snapshot`、`_validate_display_cart_checkout`、
  `_bind_store_credit_signature`、`freeze_store_credit_cart` 的 `signed_items`）。
  少改任一處，每一筆購物金結帳都會 `SignatureContentMismatch`。
- `content_version` 升為 `cart-v2` / `store-credit-signature-v2`；升版前開著的舊購物車
  送簽時給明確訊息（請重新整理），而不是缺鍵 500。
- `item_key` 納入商業性質（`GIFT:CATALOG:12`）：同一商品「買 2 ＋ 送 1」是兩個項目，
  共用鍵會被差異比對吃掉一筆。
- 折扣納入結帳冪等指紋：兩張金額不同的單不得被當成同一張重放。

---

## 9. 明確不做

依 §1 裁示：主管核准機制、`approved_by` 欄位、任何折扣／贈品上限、每日累積上限、
特定商品禁折清單。

### 對抗審查中做出的取捨（2026-08-03，共七輪）

| 議題 | 決定 | 理由 |
|---|---|---|
| DB 層 `Σ net_amount = sales.total` 不變量 | **不加** | service 與定價層已守著，既有 deferred trigger 也在守 `Σ tenders = total`；它擋的是 raw DML 留下的不一致，而單店單機不會有人手改 DB。加它需要同時整理 6 個「沒有明細的合成銷售」測試夾具，改動面大於它擋的風險。 |
| `NORMAL net_amount > 0` 的 CHECK | **不加** | 0 元的一般商品本來就賣得出去（既有行為，早於本功能），加了會把清楚的「銷售總額必須大於 0」422 變成看不懂的資料庫錯誤。 |
| 一般商品成本併入「自有庫存總成本」 | **不併** | 一般商品沒有入庫時間、進不了庫齡桶；併進總成本卻不進庫齡與總售價，兩個並列的總計就變成不同範圍，反而無法判讀。改以 `catalog_cost_value` 單獨呈現並揭露成本未知件數，維持既有的 `Σ 庫齡桶 = 總成本`。 |
| 舊版 cart-v1 快照的相容轉換 | **不做** | 購物車是 30 分鐘過期的暫存草稿、升級時機由店家決定；單店單機關店後升級就不會有未結完的單，程式永遠不會被執行到。以操作習慣取代：**升級前先確認沒有結到一半的單**。 |
| 整單折扣的尾差分配 | **最大餘數法＋每行至少留 1 元的容量限制** | 「前 N−1 筆四捨五入、最後一筆吃差額」會產生負分攤；只加最大餘數而不看每行上限，則會讓「同一籃商品能不能結帳」取決於掃描順序。 |

### 尚未處理（不影響金額正確性，屬顯示完整度）

- 退貨同意書以兩個時間點的退貨狀態組內容（併發下逐行金額與總額可能來自不同快照）。
- 庫存價值匯出的其餘欄位仍為舊口徑。


其他已知限制：

- 成交後的折扣**作廢／改單**尚未提供（只能作廢整張銷售重開）。
- 從未收過貨的一般商品沒有成本，贈品成本顯示為 0（成本未知，不假造）。
- 退貨畫面**不顯示贈品成本**：那是內部數字，不是店員決定要不要收回贈品所需要的資訊。

---

## 10. 驗證

- 純函式單元測試：`tests/test_sales_pricing.py`（20）、`tests/test_returns_refund.py`（7）
- 整合測試：贈品明細、臨時折扣、退貨與贈品退回、報表口徑、HTTP 合約與原因代碼管理
- 瀏覽器 E2E：`frontend/scripts/gift-discount-smoke.mjs`
  （加商品 → 單品折扣 → 整單折扣 → 改為贈品 → 結帳 → 退貨與贈品提示 → 兩份報表 → 設定頁）
