# 21 — 門市活動（限時促銷）系統規劃

> 狀態：**v1 C1–C4 已完成並合併 `main`**（活動核心、POS 折扣、管理 UI、成效報表與匯出）。
> §8 政策已依店主裁示落地；本文同時保留 v2 延伸方向。沿用 `CLAUDE.md`（金額 Decimal/整數元、
> 含稅標價、總額層級推稅、多分店就緒、稽核、TDD、API 合約優先）。

## 1. 目標與範圍

店主可開設**限時促銷活動**：限定時間窗（一天或任意起迄），對銷售自動套用折扣。
範例：開幕九折、週年慶九折。活動只影響**銷售（賣出）**，不影響收購（買進）撥款。

- 折扣型態 v1：**整百分比折扣**（九折 = 10% off，即 `discount_pct=10`）。固定金額折扣、買N送N 留待 v2。
- 標價含稅；折扣作用於品項含稅 `listed_price`/`unit_price` → 折後含稅單價，稅仍於**發票總額層級推一次**（§6）。
- 折後單價以 `round_ntd` 取整數元。
- 單店一次至多一個「生效中」活動（避免疊加歧義；見 §3.3）。多分店就緒：`campaigns.store_id`。

## 2. 動工前接入點（歷史盤點）

- 售價流經 `sales/service.py` 的 `_process_serialized` / `_process_catalog` / `_process_bulk`：
  目前 `unit_price`/`line_total` 直接取 `listed_price` / catalog `unit_price` / bulk `unit_price`。
  **折扣引擎注入於此**：算折後單價 → 設 `line_total`，並於 `sale_line` 記錄原價/折扣額/活動 id（稽核與報表）。
- 寄售結算來源：`_process_serialized` 蒐集 `consignment_sales=(item_id, gross, pct)`，
  `create_settlement` 以 `gross` 算 `commission`/`payout`。**§8 決策**決定這裡的 `gross` 用折後或原價。
- 結帳總額 → `split_tax_inclusive` 推稅；點數 `floor(total/100)` 自然以折後總額計（點數變少，合理）。
- 報表（Phase 6）：margin/turnover 由 `line_total`、成本推導 → 折扣自然反映為營收/毛利下降，**報表層免改**；
  另建議於 sale_line 存 `discount_amount` 供「活動成效」分析（折讓總額、帶動銷量）。

## 3. 資料模型（Alembic migration）

### 3.1 `campaigns`（每張業務表帶 `store_id`，§4）
| 欄位 | 型別 | 說明 |
|---|---|---|
| id | PK | |
| store_id | FK stores | 多分店就緒 |
| name | str(100) | 活動名稱（開幕九折…） |
| discount_pct | int | 折扣百分比 0<pct<100（守衛：100=免費不允許；0 無意義） |
| scope | enum | 適用範圍（見 §3.2） |
| applies_owned_serialized | bool | **預設 true**（店主裁示） |
| applies_owned_bulk | bool | **預設 true**（店主裁示） |
| applies_catalog | bool | **預設 false**（店主裁示，可開） |
| applies_consignment | bool | **預設 false**；可動態切換是否折寄售品（開啟時一律按比例分攤，見 §8.1） |
| starts_at / ends_at | timestamptz | 生效窗 `[starts_at, ends_at)`；瞬間以 UTC 儲存，管理畫面的 `datetime-local` 一律解讀為台灣時間並帶 offset 送 API |
| status | enum | DRAFT / ACTIVE / ENDED / CANCELLED（狀態機，§3.3） |
| created_by / created_at / updated_at | | 稽核 |

- 金額/比率守衛以 CHECK：`discount_pct` 0–99；`ends_at > starts_at`。
- **部分 unique index**：同 `store_id` 同時間窗至多一個 `ACTIVE`（DB 約束擋疊加，仿 cash_session 單一 OPEN）。
  做法：以「同店 ACTIVE 且時間窗重疊」為衝突——v1 簡化為「同店至多一個 ACTIVE」(partial unique on status='ACTIVE')，
  開新活動前需先結束舊的。

### 3.2 適用範圍 `scope`（v1）
- `ALL_ELIGIBLE`：所有「符合品項種類開關」的在售品。
- （v2 預留）`BY_CATEGORY` / `BY_GRADE`：限分類或成色帶。
- 品項種類開關（owned/consignment/catalog/bulk）獨立於 scope，先以布林控制（§8 決策預設）。

### 3.3 狀態機與生效判定
- DRAFT → ACTIVE（啟用）→ ENDED（到期或手動結束）；DRAFT/ACTIVE → CANCELLED（作廢）。
- 「生效中」= `status=ACTIVE` 且 `now ∈ [starts_at, ends_at)`。**結帳時點即時判定**（非加入購物車時鎖價），
  避免「活動已過期仍套用」。到期自動視為失效（查詢時以時間窗過濾；可另有背景/登入時 lazy 轉 ENDED）。
- 稽核：建立/啟用/結束/作廢/改折扣皆寫 `audit_log`（§5「改價」級敏感操作），MANAGER 限定。

### 3.4 `sale_line` 擴欄（折扣留痕，供退貨/報表/稽核）
- `original_unit_price`（折前含稅單價）、`discount_amount`（每行折讓額）、`campaign_id`（FK，nullable）。
- `unit_price`/`line_total` 維持為**實際成交（折後）**值——退貨退實付、報表認實收，皆以此為準，無需改下游。

## 4. 折扣引擎（`core/` 或 sales 內，純函數、有測試）

```
折後單價 = round_ntd(原含稅單價 × (100 − discount_pct) / 100)
每行折讓 = (原單價 − 折後單價) × qty
```
- 一律 Decimal、ROUND_HALF_UP、整數元；序號品 qty=1，catalog/bulk 乘 qty。
- 邊界測試：pct 邊界（1、99）、round 邊界、qty 多件、折後不得 < 0、不得 > 原價。
- 引擎輸入：品項原價、qty、品項種類、ownership；輸出折後單價 + 折讓額 + 是否適用（依種類開關/scope）。

## 5. POS 整合（結帳流程）

- `create_sale` 開頭載入「生效中活動」（一筆或無）。逐行 `_process_*` 時：
  若該行品項符合活動適用條件 → 用折後單價設 `line_total`，記錄 original/discount/campaign_id；否則原價。
- 寄售行的 `gross`（餵 settlement）依 §8 決策：折後 or 原價。
- 全程單一交易、原子；既有 idempotency（同 key 回原單）不變——折扣在建單時點定價，replay 回原單不重算。
- 並發：活動於結帳當下生效即套用；活動結束與結帳競態 → 以建單交易讀到的狀態為準（可接受；非現金一致性風險）。
- 前端 POS：顯示生效活動橫幅、折後價與折讓；收據/明細聯列出折扣（硬體代理，沿 Phase 3）。

## 6. 退貨 / 作廢互動

- 退貨退**實付（折後）**金額；`sale_line.line_total` 已是折後，現有 returns（Phase 4，目前 UI shelved）邏輯不需改。
- 寄售折後售出後退貨：沿用既有 settlement 反轉（未付→CANCELLED、已付→reclaim）；金額以建單時記錄者為準。
- 作廢：沿用現有 void（沖點數、沖購物金、反轉寄售結算）。

## 7. 報表 / 分析

- Phase 6 報表自動反映折扣（營收/毛利由折後 line_total 推）。**無需改報表計算**。
- 加值（v1 可選 / v2）：以 `sale_line.discount_amount` + `campaign_id` 出「活動成效」：折讓總額、活動期間營業額/毛利/筆數、
  與平時對比（沿 R6 trends 同源）。

## 8. 政策決策（**店主已拍板 2026-06-21**）

### 8.1 寄售品折扣 — 開關 + 一律按比例分攤（店主裁示 2026-06-21 更新）
- `applies_consignment`（預設 false）：是否對寄售品套折扣；店主每檔活動自行切換。
- **開啟時一律按比例分攤**：`gross=折後價`，抽成與 payout 都按折後縮水——**寄售人按折後價分潤、承擔折扣**
  （店主與寄售人之約定）。不再提供「店家吸收（STORE_ABSORBS）」模式（已移除 `consignment_discount_bearing`
  欄位與選項，migration e1f2a3b4c5d6 移欄）。按比例分攤下店家淨收恆 = 折後抽成 ≥ 0，**無需虧損守衛**。
- 關閉（預設）時：寄售品**不折、原價結帳**，`gross=listed_price`、結算照舊。

### 8.2 適用品項種類（預設）
店主裁示：預設 **自有序號品 + 自有散裝(E級)** 開；catalog、寄售預設關（catalog 可手動開、寄售經 §8.1 切換）。

### 8.3 折扣型態與疊加
店主裁示：**整百分比折扣、不疊加**。同店至多一個生效活動；購物金為**付款方式**非折扣、照常可用。
固定金額／買N送N／會員專屬 → v2。

## 9. 實作拆分（§8 已拍板）

- **C1 後端核心** ✅（main 7fa544f）：`campaigns` 模型 + migration + 折扣引擎（core，純函數測試）+
  campaign CRUD/啟用/結束 service+API（MANAGER、稽核、單一 ACTIVE 守衛）。
- **C2 POS 整合** ✅：`create_sale` 載生效活動套折扣、`sale_line` 擴欄（original_unit_price/
  discount_amount/campaign_id, migration d6b2c3e4f5a7）、序號/catalog/bulk 三路徑、寄售 gross 依
  §8.1 開關 + 一律按比例分攤（gross=折後、寄售人按折後分潤）+ 不變量測試。
  - **報表口徑（2026-06-21 更新）**：寄售折扣改為一律按比例分攤後，settlement.commission 本即按折後算，
    **R2/R5/C4 認列營收/毛利不再高估**，無口徑修正需求（先前 STORE_ABSORBS 高估問題隨該模式移除而消失）。
- **C3 前端**：活動管理頁（建/啟用/結束）+ POS 折扣顯示 + 收據折扣（純 UI 可委派）；**依 docs/08 §6.1
  必跑瀏覽器 e2e + 截圖**（新增 campaigns-smoke.mjs）。
- **C4 活動成效報表**：沿 Phase 6 同源（每檔活動期間的營業額/折讓/認列營收/毛利/毛利率；唯讀、CSV/XLSX）。
- 每步 TDD + 本機四道門 + 自我 adversarial review（金流：折扣 rounding、寄售金額、退貨退實付、並發、稽核）。

## 10. 不變量（以測試守護）

1. 折後單價 = round_ntd(原價 ×(100−pct)/100)，0 ≤ 折後 ≤ 原價；pct 限 1–99。
2. 稅仍於總額層級推一次：`net+tax=折後總額`，不差一元。
3. 寄售結算金額與 §8 決策一致（不折→原價；開折→一律按折後價算抽成與 payout）——抽成/payout 恆 ≥ 0（按比例分攤天然成立）。
4. 退貨退實付（折後）金額；報表營收/毛利認折後；點數依折後總額。
5. 活動生效以結帳交易讀到的狀態為準；同店至多一個 ACTIVE。
6. 建立/改/啟用/結束/作廢活動皆寫 audit_log；MANAGER 限定。
