# 35 — 餐飲內用桌號與出餐單

餐飲（`menu_items`）目前只走「加進購物車 → 結帳」，出餐這件事完全在系統外。實際營運時，
櫃檯收完錢，**做飲料的人不知道要做什麼、要送到哪一桌**。這份文件補上這條線：
結帳時記錄內用／外帶與桌號，並印出一張給店內用的出餐單。

實作分支：`feat/dinein-table-and-kitchen-ticket`。

---

## 1. 店主裁示（2026-08-16）

| 議題 | 裁示 | 代價 |
|---|---|---|
| 內用／外帶 | **要切換**，兩者都有 | `sales` 多一個 `service_mode`，POS 多一個必選步驟 |
| 出餐狀態追蹤 | **不做**，靠印出來的紙核對 | 系統查不到「這杯做了沒」；補做要另立待出餐清單頁 |
| 桌號輸入 | **設定頁維護清單，POS 出按鈕點選** | 多一個設定頁區塊；桌號沒維護就不能點內用 |
| 是否印出餐單 | **設定開關** | `settings.print_kitchen_ticket` |

### 1.1 本次**不**改變的既有規則

餐飲既有的三條限制綁的是 `sale_lines.line_type == MENU`，**與內用／外帶無關**，本次不動：

- 不累積會員點數
- 不參與門市活動折扣
- 不可用購物金折抵

也就是說 **外帶餐飲同樣不累點、不折扣、不可用購物金**。若日後要讓外帶比照一般商品，
是另一項裁示，不在本次範圍。

---

## 2. 資料模型

### 2.1 `sales` 擴充

| 欄位 | 型別 | 意義 |
|---|---|---|
| `service_mode` | `DINE_IN｜TAKEOUT`，可空 | 純二手／一般商品的單為 NULL |
| `table_no` | `varchar(20)`，可空 | **字串快照**，非 FK |

`table_no` 刻意存字串而非指向設定清單：設定頁改掉桌號後，歷史交易仍應顯示當時那一桌
（同「供應商名快照，不改寫歷史」的既有口徑）。

**DB CHECK（明寫三種合法組合，且每個比較都 NULL-safe）**：

```sql
CHECK (
     (service_mode IS NOT DISTINCT FROM 'DINE_IN' AND table_no IS NOT NULL)
  OR (service_mode IS NOT DISTINCT FROM 'TAKEOUT' AND table_no IS NULL)
  OR (service_mode IS NULL                        AND table_no IS NULL)
)
```

> **實作時踩到的坑（回歸測試已鎖住）**：Postgres 的 CHECK 只在結果為 `false` 時拒絕，
> `NULL` 一律放行。第一版寫成 `service_mode = 'DINE_IN'`，看起來「三種組合都明寫了」，
> 但 `(NULL, 'A1')` 這一列算出來是 `NULL OR false OR false` ＝ `NULL` → 照樣進得來。
> 用 `IS NOT DISTINCT FROM`（永遠回 true/false）才真的守得住。
> 守衛：`test_db_check_rejects_inconsistent_service_mode[None-A1]`。

**跨表不變量由 service 守**（DB 看不到 `sale_lines`）：

- 購物車含 `MENU` 行 ⇒ `service_mode` 必填 → 否則 422
- 購物車不含 `MENU` 行 ⇒ 不得帶 `service_mode` / `table_no` → 否則 422
- `service_mode = DINE_IN` ⇒ `table_no` 必須在 `settings.dine_in_tables` 之內 → 否則 422

口徑與 docs/32 §9 相同：跨表／需查設定的不變量放 service，DB 只守單列自洽。

### 2.2 `settings` 擴充

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `dine_in_tables` | `JSONB`（字串陣列） | `[]` | 桌號清單，順序即 POS 按鈕順序 |
| `print_kitchen_ticket` | `bool` | `true` | 結帳後是否自動印出餐單 |

`dine_in_tables` 的邊界驗證（PATCH 時）：每個元素去頭尾空白後非空、長度 ≤ 20、**清單內不重複**、
總數 ≤ 50。空清單合法（代表「還沒設定」），但此時 POS 不讓選內用（見 §4）。

> 用 JSONB 而非另立 `tables` 表：現階段桌號沒有任何附加屬性（座位數、區域、狀態），
> 建表是過度建模。`settings` 每店一列，多分店天然隔離（§4）。

---

## 3. 出餐單列印

### 3.1 新端點 `POST /print/kitchen`（hardware-agent）

出餐單是**內部作業單**，不是給客人的憑證：

- **不取店家抬頭**——不需要店名／統編／地址，也少一次對後端的 HTTP 相依
- **不印任何金額**
- **只印 `MENU` 行**：二手商品不進吧台

`KitchenTicketPayload`：

| 欄位 | 說明 |
|---|---|
| `store_id` / `sale_id` | 追溯用 |
| `service_mode` | `DINE_IN｜TAKEOUT` |
| `table_no` | 內用必填、外帶必須為 None |
| `created_at` | 結帳時間 |
| `lines` | `[{description, qty}]`，`min_length=1` |

`model_validator` fail closed（比照 `AcquisitionReceiptPayload._credit_facts_match_payout`）：
`DINE_IN` 缺桌號、或 `TAKEOUT` 夾帶桌號 → 422。**不得默默印出一張沒有桌號的內用單**。

版面（`EscposReceiptPrinter.print_kitchen_ticket`）：

```
        出  餐  單
  ─────────────────────
   內用    桌號  A3          ← 桌號放大（double height/width）
  ─────────────────────
   手沖耶加雪菲          x1
   鮮奶茶（無糖）        x2
  ─────────────────────
   #1042      08/16 14:32
```

外帶時「桌號 A3」整段換成「**外帶**」。

### 3.1.1 印到哪一台（選配第二台 EPSON）

`/print/kitchen` 印到 **`AgentDevices.kitchen_ticket_printer`**，解析規則只有一處：

| `AGENT_KITCHEN_HOST` | 出餐單去向 | 狀態頁 |
|---|---|---|
| 未設 | 收據機（櫃檯那台） | 不列管出餐機 |
| 有設 | 第二台 EPSON（廚房/吧台） | 多一台 `kitchen-1`，TCP 探測 |

- **未設時退回收據機**是硬性要求：在買到第二台之前，出餐單不得因此壞掉。
- **出餐機只印出餐單**，不印收據／明細聯／證明聯；錢櫃仍掛在櫃檯那台。
- **缺紙不得改印櫃檯那台**：店員會以為廚房收到了，那份餐永遠不會被做。
  出餐機失敗就是失敗（409/503），由畫面如實呈現。
- 出餐機**必須列管狀態**：它多半在廚房，離線了沒人看得見。

### 3.2 列印時機與失敗處理

- 結帳成功且 `print_kitchen_ticket` 為真且該單有 `MENU` 行 → **自動印，不詢問**。
  （與「商品明細」不同：明細是問客人要不要，出餐單是內部一定要。）
- 失敗**不擋流程**（交易已成立），比照 `openCashDrawer` 的既有處理，
  但提示必須明顯——吧台沒拿到單就不會做東西。
- **必須可重印**，入口兩處：POS 完成頁一顆「重印出餐單」、`/sales` 交易紀錄含餐飲的單一顆。

---

## 4. POS 流程

購物車出現第一筆 `MENU` 行時，收款區上方展開「內用／外帶」：

1. **不預設選項，必選。** 未選 → 結帳鍵停用並顯示原因（沿用既有 tender 驗證的呈現方式）。
   預設任一邊都會被慣性按過去，而桌號打錯的成本是東西送錯桌。
2. 選「內用」→ 展開桌號按鈕列（來自 `settings.dine_in_tables`），必須點一個。
3. `dine_in_tables` 為空 → 內用鍵停用，提示「請先於設定頁維護桌號清單」（fail closed，
   不讓店員自由打字繞過）。
4. 購物車移除最後一筆 `MENU` 行 → 整個區塊收起，`service_mode`／`table_no` 一併清空。

`/sales` 交易紀錄列表加「桌號」欄（外帶顯示「外帶」、無餐飲顯示「—」）。

---

## 5. 不受影響的部分

金額、稅、發票、活動折扣、購物金、會員點數、現金對帳**全部不變**——
`service_mode` 與 `table_no` 是純資訊欄位，不進入任何計算。

---

## 6. 測試

- **後端**：CHECK 三種合法組合與各種非法組合；service 的三條跨表驗證；
  `dine_in_tables` PATCH 邊界（重複／超長／超量／空白）。
- **hardware-agent**：`KitchenTicketPayload` 的 fail-closed 驗證；driver 版面（比照
  `test_escpos_receipt` 既有做法斷言送出的 ESC/POS byte 序列）。
- **前端**：vitest 覆蓋「未選內用外帶 → 結帳停用」「桌號清單空 → 內用停用」。
- **瀏覽器 E2E（CLAUDE.md §1 強制）**：`frontend/scripts/pos-dinein-smoke.mjs`，
  對真 backend + 真 Postgres 跑一次並附截圖。
