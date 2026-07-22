# 16 — 購物金（store credit）與點數規格

> 狀態：**已核准、實作中**（2026-06-11 核准；ADR-012 Accepted）。核心決策見 ADR-012。
> 進度（2026-06-13）：**SC-1 帳本核心、SC-2 收購撥款、SC-3 銷售 tender + 沖正、點數（§0）皆已實作並合併進 `main`**。**SC-4 報表、SC-5 溢價設定+建議值引擎尚未動工**（可與前端 F3 POS 並行）。各任務的 DB 層守衛實況見 §8。
> 任務切分：SC-1 帳本核心 → SC-2 收購撥款 → SC-3 銷售 tender + 沖正 →（SC-4 報表 ∥ SC-5 溢價設定+建議值引擎，可與 T19 前端並行）。SC-1～3 為 T19 POS 結帳 UI 前置（已滿足）。
> G3 閘門（仍開）：待會計師確認「禮券/儲值歸類、效期與履約保證、溢價稅務認列時點」；不阻擋建模，效期相關欄位最終值待確認（`expires_at` 仍恆 NULL）。

## 0. 點數累積規則（併入既有點數小任務，與購物金獨立但相鄰）

- **規則**：`點數 = floor(該筆銷售含稅總額 total ÷ 100)`，每筆交易計算一次，結帳交易內同步 `contacts.member_points += 點數`（有 `buyer_contact_id` 時才累積）。
- **購物金支付照樣給點**：點數以 `total` 計，與 tender 組成無關（購物金等同現金消費）。
- **收購撥款不給點**（不論現金或購物金撥款）。
- **作廢沖回**：`void` 時於同交易扣回該筆銷售曾累積的點數（同額一次；點數僅累積、無兌換，故扣回不會使餘額為負）。
- 本階段**僅累積、不兌換**（維持既有裁示）；顯示於 /contacts 與 POS 會員歸戶區。

## 1. 資料模型

### 1.1 `store_credit_ledger`（事實來源；INSERT only，ADR-012）

| 欄位 | 型別 | 約束 / 說明 |
|---|---|---|
| `id` | PK | |
| `store_id` | FK `stores.id`，index | 多分店就緒（CLAUDE.md §4） |
| `contact_id` | FK `contacts.id`，index | 帳戶主體（每店每 contact 一帳戶） |
| `entry_type` | enum `StoreCreditEntryType` | `CREDIT`／`DEBIT`／`REVERSAL`／`ADJUSTMENT` |
| `signed_amount` | NUMERIC(12,0) → `Decimal` | 整數元、非零；CREDIT 為正、DEBIT 為負、REVERSAL 與被沖正列反號、ADJUSTMENT 正負皆可 |
| `balance_after` | NUMERIC(12,0) | 該列之後的滾動餘額；恆 `>= 0` |
| `cash_equivalent` | NUMERIC(12,0)，nullable | **CREDIT 必填**：現金等值（未加溢價） |
| `premium_rate_applied` | NUMERIC(5,4)，nullable | **CREDIT 必填**：當下適用溢價率（如 0.1000） |
| `source_type` | enum `StoreCreditSourceType` | `ACQUISITION`／`SALE`／`SALE_VOID`／`ACQUISITION_ROLLBACK`／`MANUAL` |
| `source_id` | int，nullable | 對應 acquisition / sale id；`MANUAL` 為 NULL |
| `reversal_of_id` | FK self，nullable | REVERSAL 指向被沖正列 |
| `fingerprint` | String(64) | 內容 sha256（金額/帳戶/來源），冪等比對用（D-2 模式） |
| `reason` | String(200)，nullable | **ADJUSTMENT 必填**（人工校正事由） |
| `expires_at` | timestamptz，nullable | **預留**：G3 定案前恆 NULL（暫定永久不過期） |
| `created_by` | FK `users.id` | 留痕 |
| `created_at` | timestamptz | |

- 唯一約束：`(store_id, source_type, source_id, entry_type)`（`MANUAL` 因 source_id NULL 不受限，靠 audit_log 與 reason 留痕）。
- migration 附 DB trigger：對本表 `UPDATE`/`DELETE` 一律 RAISE（雙保險，應用層 repository 亦只提供 INSERT）。
- 實發購物金（需求所稱 `credit_amount`）＝ CREDIT 列之 `signed_amount`，不另存重複欄位；不變量 I-4 鎖定三值關係。

### 1.2 `store_credit_accounts`（快取；可隨時重算）

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | PK | |
| `store_id` + `contact_id` | FK，**複合唯一** | 一店一 contact 一列 |
| `balance` | NUMERIC(12,0) | 快取餘額 |
| `version` | int | 樂觀鎖版本號（每寫 +1） |
| `updated_at` | timestamptz | |

- 寫入序列化的**鎖定錨點**：所有帳本寫入先 `SELECT … FOR UPDATE` 本表該列（首寫時建列），鎖內完成「讀餘額 → 算 balance_after → INSERT 帳本 → 更新快取」。

### 1.3 `premium_rate_history`（溢價變更留痕；SC-5）

`id`、`store_id`、`changed_by`（FK users）、`changed_at`、`old_rate`、`new_rate`、`suggested_rate_at_change`（變更當下的系統建議值，冷啟動時為 NULL）、`reason`（選填）。僅 INSERT。

### 1.4 `store_credit_suggestion_log`（建議值引擎落庫；SC-5）

`id`、`store_id`、`for_date`（date，每店每日唯一）、`window_metrics`（JSONB：各視窗指標快照）、`constraint_values`（JSONB：各約束中間值 p_max1/p_max2/take-rate 導向值）、`suggested_rate`、`engine_version`（規則版本字串，演算法改版可識別）、`insufficient_data`（bool，冷啟動標記）、`created_at`。

### 1.5 settings 擴充（`StoreSettings` 加欄位；SC-5）

| 欄位 | 預設 | 說明 |
|---|---|---|
| `premium_rate` | 0.1000 | 現行溢價率（起手 +10%，可被推翻） |
| `premium_rate_min` / `premium_rate_max` | 0.0000 / 0.2000 | 上下限保護；建議值與手動值都夾在內 |
| `monthly_fixed_cash_outflow` | 0 | 月固定現金支出（整數元，手動維護；負債健康比分母） |
| `store_credit_engine_params` | JSONB（附文件化預設） | 引擎可調參數：視窗權重、α 安全係數 0.8、負債門檻 1.5/硬上限 2.5、take rate 目標帶 0.30–0.70、檔距 0.025、β 之 N 天（預設 180）、α 代理視窗 90 天、冷啟動門檻 30 天 |

僅 MANAGER 可改；`premium_rate` 變更必寫 `premium_rate_history` 與 `audit_log`。

### 1.6 銷售 tender（SC-3）

- 新表 `sale_tenders`：`id`、`sale_id`（FK）、`tender_type` enum（`CASH`／`STORE_CREDIT`）、`amount`（NUMERIC(12,0)，>0）。一筆 sale 一到多列；`Σ amount = sales.total`。
- `PaymentMethod` 枚舉擴充：加 `STORE_CREDIT`、`MIXED`；`sales.payment_method` 保留為摘要欄（單一 tender 時為該型別、多 tender 為 `MIXED`），既有報表/收據相容。
- 第一版 UI 僅單一付款方式；資料模型先支援拆分（現金+購物金）。

### 1.7 收購撥款（SC-2）

- `acquisitions` 加：`payout_method` enum（`CASH`／`STORE_CREDIT`／`SPLIT`）、`payout_cash_amount`、`payout_credit_cash_equivalent`（皆 NUMERIC(12,0)；SPLIT 時兩者皆 >0，單一方式時另一者為 0）。
- 對既有資料 migration 預設 `CASH`（歷史皆付現）。

## 2. 不變量（全部以測試守護）

- **I-1** `store_credit_ledger` 僅 INSERT；UPDATE/DELETE 在應用層不存在、DB trigger 直接拒絕。
- **I-2** `balance_after(該列) = balance_after(前一列，無則 0) + signed_amount`，且恆 `>= 0`。
- **I-3** 任一帳戶：`SUM(signed_amount) == store_credit_accounts.balance == 最新列 balance_after`（對帳工作驗證；不符即告警、不得靜默修正）。
- **I-4** CREDIT 列：`cash_equivalent > 0`、`premium_rate_applied ∈ [premium_rate_min, premium_rate_max]`、`signed_amount = round_ntd(cash_equivalent × (1 + premium_rate_applied))`（`core/money.py`）。
- **I-5** 冪等：同 `(store_id, source_type, source_id, entry_type)` 重送 → 同 fingerprint 回原列、不同 fingerprint → 409（D-2 模式）。
- **I-6** DEBIT/REVERSAL 扣方向：新餘額 < 0 → `InsufficientStoreCredit`，整筆交易回滾。
- **I-7** 所有帳本寫入持有該帳戶 `store_credit_accounts` 列鎖（並發測試：同帳戶並行入帳/扣抵不丟更新、不超扣）。
- **I-8** 入帳對象必須是會員（`contacts.roles` 含 MEMBER）；非會員選購物金撥款 → 422 引導建會員。
- **I-9** 購物金不產生任何 `cash_movements` 列；現金 tender / 現金撥款部分才走錢櫃（金額 = 現金部分，非全額）。
- **I-10** 有帳本歷史的 contact 不可硬刪（FK RESTRICT + 服務層守衛）；去識別化不動帳本。
- **I-11** ADJUSTMENT：限 MANAGER、`reason` 必填、寫 `audit_log`（誰/何時/對象/前後餘額）。
- **I-12** 點數（§0）：`floor(total/100)`、購物金 tender 照計、收購不給點、void 同交易沖回。

## 3. 整合點

### 3.1 收購撥款（SC-2）

- `POST /acquisitions` 增 `payout_method`（`CASH`｜`STORE_CREDIT`｜`SPLIT`）與 SPLIT 金額拆分。
- **同一原子交易**內：建庫存 → 現金部分 `record_movement(BUYOUT_OUT, 現金部分)` → 購物金部分 ledger `CREDIT`（`cash_equivalent=購物金部分現金等值`、套用當下 `premium_rate`）。任一步失敗整筆回滾（沿用 T7 單交易語意），**購物金入帳與收購同生共死**。
- 現金部分仍要求開帳中 `cash_session`（§7.8）；**純購物金撥款不要求開帳**（不碰現金）。
- 寄售（CONSIGNMENT）撥款屬結算階段，本輪不納入購物金（Phase 4 退貨/結算再評估）。

### 3.2 銷售 tender（SC-3）

- `POST /sales` 增 `tenders` 列表（省略時預設單一 CASH 全額，向後相容）；`Σ tenders = total` 否則 422。
- 同交易內：購物金 tender → ledger `DEBIT`（餘額不足 → `InsufficientStoreCredit` → 409，整筆不成立）；現金 tender → `record_movement(SALE_IN, 現金部分)`。
- 發票/稅不受 tender 影響：`total`/`tax` 照常（購物金是支付工具、非折扣）；點數照 `total` 累積（§0）。
- mixed cart（序號/數量/散裝）原子性沿用 T11/T12，tender 僅在尾端加兩個寫入點。

### 3.3 作廢/退款沖正（SC-3）

- 銷售作廢（既有 `POST /sales/{id}/void`）：購物金 tender → `REVERSAL`（+，入回）、`source_type=SALE_VOID`；現金 tender 沿用既有現金處理；點數沖回。
- 收購回滾/作廢（未來 Phase 4 退貨配套；本輪先定義語意）：`REVERSAL`（−，扣回）、`source_type=ACQUISITION_ROLLBACK`；**餘額不足（已花掉）→ 預設擋下（409）、轉人工**（MANAGER 以 ADJUSTMENT 處理並留事由）。
- REVERSAL 一律帶 `reversal_of_id`；同一來源列**只能被沖正一次**（唯一約束涵蓋）。

### 3.4 錢櫃/關帳邊界（D-1 對齊）

- 關帳 expected 公式**只含現金 tender 與現金撥款部分**；購物金兌付當日彙總在關帳報表「另列展示」，不入現金對帳（§5C）。

## 4. API 端點清單（依任務）

| 端點 | 方法 | 任務 | 說明 |
|---|---|---|---|
| `/contacts/{id}/store-credit` | GET | SC-1 | 餘額 + 異動歷史（分頁） |
| `/contacts/{id}/store-credit/adjustments` | POST | SC-1 | 人工校正（MANAGER、reason 必填、audit） |
| `/acquisitions`（擴充 payout 欄位） | POST | SC-2 | 撥款方式 CASH/STORE_CREDIT/SPLIT |
| `/sales`（擴充 tenders） | POST | SC-3 | 多 tender；冪等沿用 Idempotency-Key |
| `/sales/{id}/void`（擴充沖正） | POST | SC-3 | 購物金 REVERSAL + 點數沖回 |
| `/reports/store-credit/liability` | GET | SC-4 | §5A 負債/餘額/帳齡 |
| `/reports/store-credit/flows` | GET | SC-4 | §5A 發出 vs 兌付 vs 淨變化（granularity=`day`/`week`/`month`） |
| `/reports/store-credit/effectiveness` | GET | SC-4 | §5B 效益指標 |
| `/reports/store-credit/reconciliation` | GET | SC-4 | 對帳狀態（I-3 全帳戶 + 全域總負債） |
| 上述各報表 `?format=csv|xlsx` | GET | SC-4 | 匯出；檔內含產生時間/區間/店別 |
| `/settings`（擴充 §1.5 欄位） | GET/PATCH | SC-5 | premium_rate 變更寫 history + audit |
| `/settings/premium-rate/history` | GET | SC-5 | 變更留痕查詢 |
| `/store-credit/premium-suggestion/today` | GET | SC-5 | 當日建議值（無當日 log 即 lazy 計算落庫後回傳） |

對帳工作：以排程（或 reconciliation 端點觸發）執行 I-3 全帳戶驗證 + `全域總負債 = Σ 正餘額`，不符寫告警（log + 面板提示）。

## 5. 報表欄位定義（全部可從帳本推導）

### 5A 購物金負債報表

| 欄位 | 定義 |
|---|---|
| `total_outstanding` | 即時未兌付總負債 = Σ 各帳戶正餘額 |
| `per_member` | 各會員餘額 + 異動歷史（同 SC-1 查詢） |
| `aging_buckets` | 未兌付餘額按**發出時間**分桶：<30 / 30–90 / 90–180 / 180–365 / >365 天；扣抵以 FIFO 沖銷發出列（報表推導用，不入帳本） |
| `issued` / `redeemed` / `net_change` | 期間內購物金流量淨額：`issued` = CREDIT + ACQUISITION_ROLLBACK 沖正額；`redeemed` = DEBIT（絕對值）+ SALE_VOID 沖正額（會抵銷已作廢兌付）；沖正依自身 `created_at` 歸屬發生期間，不回寫原始期間；`net_change = issued − redeemed`；granularity=`day`/`week`/`month` |
| `liability_health_ratio` | `total_outstanding ÷ monthly_fixed_cash_outflow`（分母為 settings 手動值；=0 時顯示 N/A） |
| `distributable_cash`（供分潤安全水位） | `現金水位 − total_outstanding`（現金水位來源於現金對帳；此欄為展示公式，現金水位輸入屬報表參數） |

### 5B 效益指標報表（欄位命名與損益敏感度 Excel 模型一致）

| 欄位 | 性質 | 定義 |
|---|---|---|
| `take_rate` | 直接量測 | 期間內收購筆數中選購物金（含 SPLIT）的比例 |
| `avg_premium_rate` | 直接量測 | `Σ(signed_amount − cash_equivalent) ÷ Σ cash_equivalent`（CREDIT 列） |
| `beta_retention` | **估計值** | 沉澱率 β：發出滿 N 天（預設 180，可調）之 CREDIT 中，至今仍未被 FIFO 沖銷的金額比例；UI 標示「估計值」 |
| `excess_spend_rate` | 直接量測 | 含購物金 tender 的銷售中，`Σ 現金 tender ÷ Σ total` |
| `alpha_incremental` | **估計值（代理法）** | 新增比例 α 估計，定義見 §5B-α；一律標示「估計值（代理法）」，**不准呈現為精確值** |
| `gross_margin_m` | 直接量測 | 實際毛利率 m =（買斷毛利 + 寄售抽成）÷ 銷售收入（期間） |
| `delta_per_1000` | **估計值** | `Δ = 1000 × [1 − (1−β)(1+p)(1−α·m)]`；p 取期間 `avg_premium_rate`；標示估計值 |

#### §5B-α：α 代理法定義（明文，含侷限）

- **無法直接量測的原因**：α（「若不給購物金、這筆消費就不會發生」的比例）是反事實，帳本看不到。
- **代理定義**：對期間內每筆購物金兌付（DEBIT），檢視該會員於**對應 CREDIT 入帳前 90 天**（`alpha_proxy_window`，可調）的消費紀錄：
  - 消費筆數 `< 2` **或** 會員建檔距入帳 `< 90 天` → 該兌付歸類「新增傾向高」；
  - 其餘 → 「既有消費移轉傾向高」。
  - `alpha_incremental = 新增傾向高之兌付金額 ÷ 全部兌付金額`。
- **侷限（文件與 UI 都要說）**：代理假設「低頻/新會員的消費較可能由購物金誘發」，無法驗證個體反事實；季節性與商品結構變化會干擾；樣本小時波動大（期間兌付 < 30 筆時 UI 加註「樣本不足」）。僅供敏感度模型輸入，不得作為精確損益依據。

### 5C 既有報表整合

- 關帳報表維持**純現金**；新增「本日購物金兌付彙總」獨立區塊（Σ DEBIT 當日），僅展示、不入 expected。

## 6. 溢價率設定與每日建議值引擎（SC-5）

定位：溢價率屬**金錢級設定**——直接影響負債發生速度，比照金錢嚴格度（型別、留痕、上下限、二次確認）。

### 6.1 設定與留痕

- `premium_rate` 現值 + `premium_rate_min/max`（預設 0%–20%）夾擠；僅 MANAGER 可改；每次變更寫 `premium_rate_history`（§1.3）+ `audit_log`。
- 每筆 CREDIT 記 `premium_rate_applied`（§1.1）→ 歷史完全可重現。

### 6.2 建議值引擎（deterministic、規則式、可審計）

- **純函數**：輸入 = 各回看視窗的歷史指標（從帳本/銷售推導），輸出 = 建議溢價率 + 全部約束中間值。無隨機、無黑盒；同輸入恆同輸出（`engine_version` 標記規則版本）。
- **回看視窗**（`engine_version=sc5b-1.1`）：皆以台灣完整曆日的 `[from, to)` 計算，
  截止於「今天台灣 00:00」；昨日／前 7／30／90 個完整日不因當日首次開頁時間而變動。
  去年同期對齊台灣同月日並取 ±`yoy_halfwidth_days`（兩端日期皆納入，故預設 ±15
  為 31 個完整日）；2/29 對到非閏年時收斂為 2/28。各視窗分別算 `take_rate`、
  `avg_premium_rate`、`beta_retention`、`alpha_incremental`、`gross_margin_m`、負債比；UI 全部展示。
- **綜合指標**＝視窗加權混合（權重可調，預設：昨日 0.05／7 天 0.25／30 天 0.40／90 天 0.20／去年同期 0.10，總和 1；視窗無資料時權重重新正規化）。
- **約束邏輯**（以綜合指標計，全部中間值落庫）：
  1. **毛利約束上限 `p_max1`**：要求損益兩平 α\*（`α* = p / ((1+p)·m)`）不超過 `α̂ × 安全係數`（預設 0.8）。解出：`p_max1 = c·m / (1 − c·m)`，其中 `c = 0.8 × α̂`；若 `c·m ≥ 1` 視為無上限（取 `premium_rate_max`）。
  2. **負債約束上限 `p_max2`**：`ratio = total_outstanding ÷ monthly_fixed_cash_outflow`。階梯：`ratio ≤ 1.5` → 不設限（= max）；`1.5 < ratio ≤ 2.0` → 現值 × 0.5；`2.0 < ratio ≤ 2.5` → 現值 × 0.25；`ratio > 2.5` → **0%（暫停溢價）**。分母為 0（未維護）→ 本約束跳過並於輸出標註。
  3. **選用率導向值**：`take_rate < 0.30`（目標帶下緣）→ 現值 +2.5pp；`> 0.70` → 現值 −2.5pp；帶內 → 維持現值。
  4. **最終建議** = `min(p_max1, p_max2, 選用率導向值)`，夾在 `[premium_rate_min, premium_rate_max]`，捨入到 0.5pp。
- **冷啟動**：資料天數 < 30（`cold_start_min_days`）→ 引擎不計算，固定顯示起手值 10% 並標示「資料不足，採用預設值」（log 記 `insufficient_data=true`）。
- **排程與落庫**：每店每日一筆 `store_credit_suggestion_log`（§1.4）。執行方式：**當日首次讀取（POS 開帳/面板載入）時 lazy 計算並冪等落庫**（純函數 + 每店每日唯一鍵，重複觸發無害）；不引入額外排程基建。POS 開帳面板顯示當日建議值。
- **手動調整 UI**（進 docs/10 /settings）：調整畫面**並列**「當日建議值 + 各約束中間值摘要」（p_max1/p_max2/take-rate 導向、負債比），店主可一鍵採納或自行輸入（夾在 min/max）。
- **建議值永不自動生效**——所有變更都必須人工確認並留痕。

## 7. 測試重點（對應 docs/06）

- 單元：I-1～I-12 全數；引擎純函數（各約束邊界、權重正規化、冷啟動、捨入）；α 代理分類；FIFO 帳齡分桶。
- 整合：收購三種 payout（原子回滾）、銷售多 tender（不足 409、現金部分入錢櫃）、void 沖正、冪等重送、并發（同帳戶並行入帳/扣抵）。
- 端對端：收購選購物金 → 餘額查詢 → 消費扣抵 → 作廢入回 → 對帳全綠。

## 8. DB 層守衛（實作後補記，2026-06-13）

§2 的不變量原規劃「以測試守護」。實作經多輪 Codex adversarial review 後，把守衛**下沉到 DB 層**：以 `DEFERRABLE INITIALLY DEFERRED` 約束觸發器在 **COMMIT 時**把關，讓「直接下原生 DML 繞過 service」也擋得住（具 DDL 權限、能停用觸發器者除外，屬範圍外）。正常路徑（header 先插、明細/分錄後插）不受影響——延遲到提交才驗，同交易內彼此可見。

### 8.1 帳本本身（SC-1，`store_credit_ledger`）

- **immutable**：UPDATE/DELETE 一律 RAISE（I-1）。
- **滾動餘額鏈守衛**：先 `PERFORM … FOR UPDATE` 鎖帳戶列再驗 `balance_after = 前一列 + signed_amount` 且尾插（I-2）；快取 `AFTER INSERT` 以 `balance_after` 覆寫自癒（I-3）。
- **CREDIT 經濟守衛**：`cash_equivalent > 0`、`premium_rate_applied ∈ [0, 0.20]`、`signed_amount = ROUND(ce × (1+p))`（I-4）。
- **沖正守衛**：不可沖沖正列、金額為原列負值、`source_type` ↔ 原列事件綁定、`source_id` 等於原列、一列只能被沖一次（部分唯一索引）（I-6 反向）。
- **`id` 採 `GENERATED ALWAYS`**、複合租戶 FK（`contacts(id, store_id)`、ledger→accounts、沖正三欄自參考）。
- **ADJUSTMENT** 需 `Idempotency-Key`（MANUAL 無 source_id，以部分唯一索引防重複）。

### 8.2 收購 ↔ 帳本（SC-2，`acquisitions`）

- **credit 腿 ↔ 帳本雙向綁定**：收購頭的購物金腿必須對應一筆**同店、同對象、等額**的 `ACQUISITION/CREDIT` 分錄；反之該分錄也必須對應收購頭。身分恆等不可變更（擋事後改成 CASH／歸零／改 store/contact／刪除／孤兒分錄／對象錯置）。
- **庫存背書**：產生購物金負債的收購必須有**同店、店家自有**（`serialized_items.ownership_type='OWNED'`／`bulk_lots.consignor_id IS NULL`）的庫存實體，且成本加總 = 撥款總額；收購側與庫存側（`serialized_items`/`bulk_lots` 的 INSERT/UPDATE/DELETE）雙邊強制——擋空殼收購鑄造負債、事後改成本/搬走/刪除/用他店或寄售品湊數。

### 8.3 銷售 ↔ 帳本（SC-3，`sale_tenders` / `sales`）

- **收款對平**：`Σ sale_tenders.amount = sales.total` 且 `total > 0`（後者改以延遲守衛，因 CHECK 不可 deferred 且建單先插 `total=0` placeholder）。
- **購物金收款 ↔ 帳本 DEBIT 雙向綁定**：`STORE_CREDIT` 收款金額必須對應一筆**同店、同買方、等額**的 `SALE/DEBIT` 分錄；反之 `SALE/DEBIT` 也必須對應一筆等額收款（擋孤兒扣抵、跨店借殼 `source_id`、對象/金額錯置）。收款被搬到別的 sale 時，**來源 sale 也重驗**。
- **作廢 ↔ 沖正雙向**：銷售為 `VOID` 且有購物金扣抵時，必須有對應 `SALE_VOID/REVERSAL`；反之 `SALE_VOID` 沖正只能對應**已作廢**的同店銷售。
- **收款租戶複合 FK**：`sales` 增 `UNIQUE(id, store_id)`，`sale_tenders` 以 `(sale_id, store_id)` 複合 FK 綁定，擋跨店湊收款。

### 8.4 已留痕的邊界

- `Numeric(12,0)` 在 CHECK 前先捨入（全專案 §6 慣例）——service 整數守衛 + reconcile 偵測補位。
- 上述守衛是**縱深防護**，不取代 service 層驗證與測試；service 仍是正常路徑的第一道關。
