# 44 — 線上掃碼點餐（Cloudflare）＋彈性菜單

狀態：**規格草案，待店主審閱**（2026-10-01）。決策紀錄見 `adr/ADR-027-online-ordering-on-cloudflare.md`。

參考：店主提供的〈露坑｜店內掃碼點餐與 POS 整合計劃書 v1.0〉。本文件**不照抄**，只取
適用的原則；計劃書第 5 節的「POS 現況盤點」在本專案已有答案（POS 為自製、可直接改），
直接寫進下面的設計。

---

## 1. 店主裁示（2026-10-01）

| # | 問題 | 裁示 |
|---|------|------|
| 1 | 付款 | **現金在櫃台付；LINE Pay 在客人手機上直接付（線上付款）** |
| 2 | 彈性菜單用在哪 | **POS 與線上共用同一套菜單**（選項群組、加價、分類、照片） |
| 3 | 程式放哪 | monorepo 新增最上層資料夾 **`online-order/`**（偏離 05 結構，已獲同意） |
| 4 | 網域 | 還沒買；先用 Cloudflare `*.workers.dev` 測試網址，驗收後再買網域綁定 |
| 5 | 部署 | 除必要花費外，以 Cloudflare 免費方案為主；**POS 照舊在店內 MacBook，不搬上雲** |
| 6 | 出餐狀態（Q1） | **不追蹤**，沿用 docs/35 |
| 7 | LINE Pay 線上資格（Q2） | 店主向 LINE Pay 確認正式環境；沙盒已實測（§4.4.1） |
| 8 | 線上已付自動成立銷售（Q3） | **自動** |
| 9 | 零售商品／庫存（Q4） | **零售商品（咖啡豆、濾掛…）也上線上；甜點、咖啡等餐飲也要能設數量，線上與現場庫存同步**（§3.7） |
| 10 | 現金單逾時（Q5） | 30 分鐘提醒、**不自動取消** |
| 11 | 資安 | 網址會被攻擊、惡意利用、重複送單灌爆 POS，**都要防**（§8） |

### 1.1 既有規則**不變**

- 餐飲（`line_type = MENU`）不累點、不套門市活動、不可用購物金折抵（`lu-camp-menu-fnb` 裁示）。
  線上點餐的餐飲同樣受這些規則約束。
- ~~餐飲不扣庫存~~ → **2026-10-01 改**：餐飲品項與選項可選擇性設定數量（不設＝不限量，即舊行為），見 §3.7。
- 出餐狀態不追蹤、靠紙本出餐單核對（docs/35 裁示）。線上單沿用：成立銷售時印出餐單。
- 金額一律含稅整數元、稅在發票總額層級推算（CLAUDE.md §6）。
- **POS 是唯一的帳**：營收、發票、退款、日結都只在 POS。雲端只是「收單窗口」。

---

## 2. 整體架構

```
客人手機 ──https──▶ Cloudflare Worker（online-order/）
                     ├─ 靜態資產：點餐頁（菜單／購物車／訂單狀態）
                     ├─ /api/*：送單、查單、LINE Pay 線上付款（request/confirm）
                     ├─ D1：已發佈菜單快照、桌位、線上訂單、付款、事件
                     └─ R2：菜單照片
                          ▲
                          │  只有「店內往外連」：POS 主動推菜單、主動拉訂單（HMAC 簽章）
                          │
店內 MacBook：backend（FastAPI＋Postgres）＋ frontend（POS）＋ hardware-agent（印表機）
```

- **路線 B（計劃書 5.2）**：雲端永遠不連進店內。店內 backend 定時呼叫 Worker 拉新單、
  回報狀態；不開 port、不用 Tunnel、客網隔離不受影響。
- 店內網路斷線時：Worker 偵測到 POS 太久沒來拉單 → **自動暫停接單**，點餐頁顯示
  「請至櫃台點餐」（§7）。
- 雲端只存「訂單＋付款」這段必要資料，不存會員、不存個資。

### 2.1 Cloudflare 免費額度（2026-10-01 查官方文件）

| 服務 | 免費額度 | 本案用量估計 |
|------|---------|------------|
| Workers | 每日 100,000 次動態請求 | POS 每 5 秒拉一次 × 12 小時 ≈ 8,640 ＋ 客人操作 ≈ 數千 |
| 靜態資產 | 不計請求數 | 點餐頁 JS/CSS |
| D1 | 每日讀 500 萬列／寫 10 萬列；單庫 500 MB | 遠低於 |
| R2 | 每月 10 GB 儲存、Class A 100 萬、Class B 1000 萬次，對外流量免費 | 菜單照片 < 100 MB |

照片透過 Worker 從 R2 讀取，所以讀照片也會算到 Workers 請求數。點餐頁會設長效快取（照片網址
帶內容雜湊、內容不可變）來降低用量。R2 啟用時可能要先綁付款方式，**實作時以帳號頁面實測為準**。

### 2.2 必要花費

- LINE Pay **線上**通路的交易手續費（依合約）。可能需要另外申請線上收款，見 §10 Q2。
- 網域（每年續約；驗收後才買）。
- 免費額度只要超過就要升級 Workers Paid（最低 US$5/月）。用量到 70% 時在 POS 顯示提醒。

---

## 3. 彈性菜單（POS 與線上共用）

### 3.1 資料模型（Postgres，需 migration）

現行 `menu_items` 是「每種變化各一塊磚」的扁平結構。改成：

```
menu_categories      分類（咖啡／甜點／手沖體驗…）：store_id, name, sort_order, archived_at
menu_items           ＋ category_id(FK, 可空)、description、photo_key(可空)
                       unit_price 改稱「基本價」語意不變（含稅整數元）
menu_option_groups   選項群組（溫度／豆種／加購…）：store_id, name,
                       min_select, max_select（必選單選＝1/1；可選多選＝0/N）, archived_at
menu_options         選項：group_id, name, price_delta（整數元，≥ 0）,
                       is_available（單一選項售完，例如某豆種沒了）, sort_order, archived_at
menu_item_option_groups  品項 ↔ 群組（多對多＋排序）：一個群組可被多個品項共用
```

- **群組可共用**：「溫度（冷/熱）」建一次，美式、拿鐵都掛上去；改一次兩邊同步。
- **不同品項要不同豆子清單**（計劃書：烏干達不列入手沖）→ 建兩個群組
  「店員沖豆種」「自助手沖豆種」。第一版**不做**「同一群組依品項隱藏個別選項」，避免規則難懂。
- 舊的 `category` 字串欄：migration 把既有字串轉成 `menu_categories` 列後移除。
- 既有扁平品項**原樣保留、照常可賣**（沒掛群組＝沒有選項）。要不要合併「冷美式／熱美式」
  由店主在管理頁自己整理，**系統不自動合併**（歷史 `sale_lines` 指向舊品項，自動合併會改寫歷史）。

### 3.2 計價

```
單價 = 品項基本價 + Σ 所選選項 price_delta      （整數元，Decimal）
行金額 = 單價 × 數量
```

- 計價**只在後端**做（POS backend、Worker 各自依菜單資料驗算），永遠不信任客戶端送來的金額。
- `sale_lines` 新增 `menu_options_snapshot`（JSONB：`[{group, option, option_id, price_delta}]`），
  同一品項不同選項是**不同行**。日後改選項名稱或價錢不影響歷史收據與報表。
- **選項寫進品名**（O1b 實作）：`sale_lines.description` 存「拿鐵（冰、燕麥奶）」，收據、出餐單、
  電子發票品名、客顯不必另改就會顯示選項；品名欄由 150 放寬為 300，超過以「…」截斷。
  品名順序依群組掛載順序、群組內選項順序，與客戶端送來的順序無關。
- **撞名時帶群組名**（Codex 對抗審查第一輪）：所選選項有同名者（甜度「正常」、冰量「正常」），
  撞名的那幾項改成「甜度正常」「冰量正常」；其餘維持短格式。
- 購物車項目鍵與冪等指紋：**沒選項時維持舊形狀**（`MENU:{id}`），有選項才附上排序後的選項 ID。

### 3.3 驗證（後端拒絕，不只靠前端隱藏）

- 每個群組所選數量需介於 `[min_select, max_select]`；選項必須屬於該品項掛的群組。
- 品項或選項已停售、已封存 → 拒絕（422），訊息講人話：「熱拿鐵目前停售」。
- 單筆訂單數量上限（設定，預設每行 20、每單 50 件），避免惡意大單。

### 3.4 照片

- 在 POS「餐飲菜單」管理頁上傳（MANAGER）。允許 JPEG/PNG/WebP/HEIC（手機拍的），
  上限 10 MB。
- 後端轉成 WebP、長邊縮到 1200px、去除 EXIF（含 GPS），檔名用**內容雜湊**
  （`menu/<sha256>.webp`）。需要新增影像處理套件，實作時先確認版本與 HEIC 支援再引入。
- 照片在店內 backend 存一份（POS 離線也能顯示），「發佈菜單」時一起推到 R2。
  **存在資料庫**（`menu_photos`，店主 2026-10-02 裁示）：每晚備份與還原演練自動涵蓋。
  店內讀取 `GET /api/v1/menu-photos/{sha256}.webp` 不需登入（`<img>` 帶不了 Bearer；照片本來就要公開）。
  雲端只推有被引用、而且還沒推過的雜湊（冪等）。

### 3.5 發佈到線上

- 管理頁「發佈到線上點餐」按鈕：後端組出**菜單快照**（分類、品項、群組、選項、價格、照片鍵、
  營業設定），版本號遞增，推到 Worker `POST /integration/menu`。
- **售完／停售**屬於即時狀態，不必等發佈：切換時自動推一則輕量更新（只帶可售狀態）。
  推送失敗時排入重試，畫面顯示「線上菜單尚未同步」。
- 客人送單時 Worker 用**目前生效的快照**驗價；單子帶著 `menu_version` 進 POS。

### 3.6 POS 點餐畫面

- 菜單磚改成依分類分頁，磚上可顯示照片。
- 點有選項的品項 → 彈出選項視窗（必選群組沒選不能加入）→ 數量 → 加入購物車。
  沒有選項的品項維持現行「直接選數量」。

### 3.7 庫存（線上與現場同一份）

**唯一的庫存在 POS（Postgres）**。雲端不自己記數量，只顯示 POS 推上來的「可售數量」，
真正扣減與保留都由 POS 判定。

| 對象 | 數量從哪來 | 說明 |
|------|-----------|------|
| 餐飲品項（甜點、咖啡…） | `menu_items.stock_qty`（新欄，可空） | 空＝不限量（現行行為）；有值＝每賣一份扣 1。店員在 POS 隨時改（例：今天戚風做 8 份） |
| 餐飲選項（某豆種） | `menu_options.stock_qty`（新欄，可空） | 例：某支豆子只剩 6 杯份；選到就扣 |
| 零售商品（豆、濾掛） | 既有 `catalog_products.quantity_on_hand` | 管理頁勾「開放線上販售」才會出現在線上菜單 |

- **每日限量（O1c 實作，2026-10-01 裁示）**：品項／選項可勾「每日限量」（管理者，菜單頁）；勾了就每天
  開店自動歸零（以 `stock_day` 是不是今天判斷，不靠排程），店員在**開店前檢查頁**填當天份數
  （填 0 也算填過；沒填完檢查不算完成，可略過）。沒勾＝不限量。
- **營業中調整**：「改成 N」附上畫面上看到的數字，期間被結帳改過就 409 請店員重看；「+1」＝補貨、
  「−1」要選**報廢**或**盤點校正**，記進 `menu_stock_adjustments`（之後統計每日報廢）。加減與結帳扣量
  都是單一句條件式 UPDATE，兩邊同時搶最後一份只成交一筆（真併發測試）。
- **作廢加回**：同一營業日、且份數版本（`stock_generation`，每次「改成」或切換限量 +1）沒變才加回——
  賣出後店員按過「改成」＝已實際數過，再加回會多算（Codex 對抗審查）。報廢不是作廢：還沒賣出就壞掉用「−1 報廢」。
- POS 磚顯示「剩 N 份」；0 份時分「售完」與「今天未填份數」，都不能點（後端仍以扣量為準）。
- 二手單件商品（serialized）、散裝批**不上線**。
- 數量變成 0 → 線上自動顯示售完；POS 磚也灰掉。
- **保留（reservation）**：POS 新增 `stock_reservations`（線上訂單、對象、數量、到期時間）。
  可售 = 庫存 − 未到期保留。**現場結帳也看可售數**，所以線上保留住的最後一份，櫃台不會再賣出去。
- 保留只因下列事件結束：成立銷售（轉成正式扣減）、取消、到期。到期時間：LINE Pay 未付款
  10 分鐘、現金單 30 分鐘（到期只放掉保留，**單子不取消**；客人之後來付時，帶入結帳會重新檢查庫存）。
- 每次 POS 數量或保留有變 → 立即推一則「可售數量」更新到雲端（失敗排入重試）。
  O4 實作以每 5 秒重取當前狀態達成，避免各銷售／退款路徑漏送；先持久化 revision 與內容再傳送，晚到版本拒收、失敗重試。客人菜單每 15 秒刷新，真正庫存仍由 POS 匯入時原子保留。

**O4 實作定案（2026-10-02）**：保留＝POS 拉到單時在同一交易內**直接扣每日限量份數**並記一筆
`stock_reservations`（記下扣到的份數版本），回報 `HELD`／`REJECTED`。櫃台看到的剩餘份數立刻少了，不會把線上
保留的那份再賣掉。帶入結帳成立銷售時，同一交易內先把保留**加回**、再照一般結帳**扣掉**（淨額不變、交易內持鎖，
別人插不進來），既有的結帳／作廢／退款流程完全不用改。現金單保留 30 分鐘到期就加回（單子不取消，之後來付時
帶入結帳會重新檢查庫存）。

**送單時的庫存確認（避免兩邊同時賣出最後一份）**

- 訂單**只含不限量品項** → 雲端直接接單，不等 POS。
- 訂單**含有限量品項** → 雲端先擋明顯不夠的（依推上來的數字），通過後狀態 `HOLD_REQUESTED`，
  客人畫面顯示「確認庫存中…」。POS 下一次拉單（3 秒內）在**一個交易內**建立保留，回報
  `HELD` 或 `REJECTED`（哪一項不夠）。`HELD` 之後才能付款／成立現金單。
- POS 離線時有限量品項無法確認 → 依 §7 自動暫停接單。
- 線上 LINE Pay 的扣款發生在 Worker 呼叫 confirm 時（客人在 LINE Pay 按付款只是授權）。
  Worker confirm 前先確認保留還沒過期；過期就**不 confirm**＝不扣款，請客人重新下單。
  所以「錢收了但東西已經沒了」的情況不會發生。

### 3.8 線上單的價格規則

線上沒有會員登入，所以線上單**一律原價、不累點、不套門市活動、不可用購物金**（零售商品也一樣）。
客人想用會員或活動，請到櫃台點。理由：線上顯示的價格必須等於最後實收，不能結帳時才變價。
（店主若要改，再另行裁示。）

---

## 4. 線上點餐流程

### 4.1 桌位與 QR

- 桌位沿用 `settings.dine_in_tables`（docs/35），每桌另產生**不可猜的桌位碼**：
  `https://<host>/t/<tableCode>`。可在設定頁「重發」某桌的碼，舊碼即失效。
- 也提供一個不綁桌的「外帶」碼（`service_mode = TAKEOUT`）。
- 桌位碼**不是身分證明**：只決定桌號，不能用來查任何訂單。

### 4.2 客人端畫面

1. 掃碼 → 菜單（分類、照片、價格、售完灰掉）。不用登入、不用 App。
2. 選品項 → 選項 → 數量 → 購物車（可加備註，限 60 字）。
3. 選擇付款：**LINE Pay（現在付）**或**現金（到櫃台付）**。
4. 選填發票資訊：手機載具／統編／捐贈碼（只驗格式，和 POS 一致）。不填＝印紙本。
5. 送出 → 訂單頁（網址帶訂單專屬權杖，可加入書籤；重新整理不會重送）。

### 4.3 現金單

```
送單（含限量品項者先 HELD）→ 雲端 order(payment=UNPAID) → POS 拉到「線上訂單」清單（響提示音＋數字徽章）
→ 客人到櫃台 → 店員點該單「帶入結帳」→ 購物車預填品項／選項／桌號／內用外帶／發票資訊
→ 照現行流程收現金結帳（印收據、出餐單、開發票）→ POS 回報雲端 PAID
```

- 帶入時 POS **用 POS 目前的菜單重新計價**。和客人看到的金額不同時（例如剛好改價）
  要明顯提示差額，由店員確認，不默默改價。
- 現金單**付款前不製作**（出餐單在結帳時才印）。
- 未付款逾時：超過 `online_order_unpaid_timeout_min`（設定，預設 30 分鐘）在 POS 標黃提醒，
  店員可「取消」。第一版**不自動取消**（避免客人正在櫃台付款時單子消失）。

### 4.4 LINE Pay 線上單（關鍵流程）

LINE Pay Online API v4（2026-10-01 查官方文件：`POST /v4/payments/request`、
`POST /v4/payments/{transactionId}/confirm`、`POST /v4/payments/{transactionId}/refund`；
簽章方式與現行 Offline v4 相同）。

```
① 客人送單（選 LINE Pay）
   Worker：驗價（含限量品項者先等 POS 回 HELD）→ 建 order(payment=PENDING) ＋ payment row（orderId = 線上訂單 ID）
   → 呼叫 request（amount、packages、redirectUrls.confirmUrl/cancelUrl 指回 Worker）
   → 回 paymentUrl，手機跳 LINE Pay（App 或網頁）
② 客人在 LINE Pay 按付款 → 導回 confirmUrl
③ Worker：先查本地狀態（重複導回直接顯示結果）→ 確認保留未過期 → 呼叫 confirm（金額必須等於訂單金額）
   → 0000：payment=PAID、記下 transactionId（**字串**保存，19 位整數在 JS 會失真）
   → 失敗／取消：payment=FAILED/CANCELLED，訂單頁顯示「付款未完成，可重新付款或改到櫃台付現」
④ POS 拉到 PAID 的線上單 → **自動成立銷售**：
   SaleLine＝線上單明細快照（客人已付的價格為準）、tender＝LINE_PAY（channel=ONLINE，
   帶 transactionId，**不再向 LINE Pay 扣款**）、依客人填的載具開發票（沒填印紙本）、
   印出餐單（＋紙本發票）→ 回報雲端 IMPORTED（含 POS 銷售編號）
```

- **已付款的單，以客人實付金額為準**：POS 不重新計價（錢已經收了）。若當下 POS 菜單價與
  快照不同，只在線上訂單清單標註「以線上價格成交」，不擋。
- **confirm 的回應遺失**（Worker 逾時）：不可判為失敗。payment 標 `CONFIRMING`，之後用
  查詢 API（實作時查證 v4 online 對應端點）補查到確定結果為止；客人頁顯示「付款確認中」。
- **付了錢但 POS 一直沒拉走**（MacBook 關機）：雲端單保持 PAID；POS 恢復後補拉，按時間順序
  成立銷售、補印出餐單。POS 畫面在這種補拉時要醒目提示「這是 N 分鐘前已付款的單」。
  發票開立時間落在補拉時（Amego 流程不變）。
- 自動成立銷售**不需要開帳**（非現金不進抽屜，現行規則）。

#### 4.4.1 沙盒實測紀錄（2026-10-01，用現有 Offline 測試通路的 Channel）

- `POST /v4/payments/request` → `0000`，回 `paymentUrl.web`／`paymentUrl.app`／`transactionId`。
  **現有測試 Channel 可以呼叫線上付款 API**（正式環境是否開通仍待店主向 LINE Pay 確認）。
- `GET /v4/payments/requests/{transactionId}/check` → 客人未付時回 `0000 reserved transaction.`
- 回應 JSON 的 `transactionId` 是 19 位**數字**（例 `2026100102385323710`），超過 JS 安全整數。
  Worker（JS）**必須**從原始文字取出字串，不可用 `JSON.parse` 後的數字。
- **完整走通（店主手機掃碼授權）**：check 由 `0000 reserved` → `0110 authentication is done`（客人已授權、
  **尚未扣款**）→ Worker 呼叫 `confirm {amount:150, currency:"TWD"}` → `0000`（payStatus `CAPTURE`）→ check 變
  `0123 completed transaction` → `refund {}` 全額退 `0000` → 再退一次回 `1165 already refunded`
  （與 Offline 相同，現有退款冪等邏輯可沿用）。`GET /v4/payments?transactionId=` 可查明細與退款紀錄。
- 補查用的狀態碼：`0000` 等客人付款、`0110` 可 confirm、`0123` 已完成（confirm 回應遺失時據此收斂，§4.5 C7）。
- 沙盒付款頁要用**真的 LINE 帳號**登入或用 LINE App 掃 QR 授權，無法全自動化。
  自動測試改用假 LINE Pay 伺服器；真沙盒驗收由店主手機掃碼配合。

### 4.5 一致性（全部要有測試）

| # | 情境 | 保證 |
|---|------|------|
| C1 | 客人連按兩次送出、斷線重送 | 送單帶冪等鍵（前端存 localStorage）；同鍵回同一張單 |
| C2 | LINE Pay 導回兩次／重新整理 confirm 頁 | 同一 payment 只 confirm 一次（D1 條件更新），第二次直接回結果 |
| C3 | POS 拉單後 commit 前當機、重拉 | `online_orders.remote_order_id` **唯一**；`sales.online_order_id` **唯一** → 同一線上單永遠只有一筆銷售 |
| C4 | POS 回報雲端失敗 | 本地先 commit、回報排入持久化重試佇列；雲端以 IMPORTED 冪等接收 |
| C5 | 價格在送單和結帳之間變動 | 現金單：結帳前提示差額；LINE Pay 單：以已付金額成交 |
| C6 | 已付款後要退 | 一律在 POS 作廢／退貨 → 呼叫 LINE Pay **Online** refund（沿用 durable 退款日誌，docs/30） → 回報雲端 REFUNDED。**禁止**在雲端直接改付款欄位 |
| C7 | LINE Pay 回 0000 但我方寫 D1 失敗（孤兒收款） | Worker 先寫 `CONFIRMING` 再呼叫 confirm；寫回失敗則留 CONFIRMING，由補查流程收斂；POS 端「線上付款對帳」列出超過 10 分鐘仍未定的單 |
| C8 | 兩台 POS 同時拉單 | 匯入以唯一鍵防重；成立銷售以 `online_order` 列鎖序列化 |
| C9 | 線上與櫃台同時搶最後一份 | 保留在 POS 單一交易內建立；櫃台看「庫存 − 保留」；只有一邊成功 |
| C10 | LINE Pay 授權了但保留已過期 | Worker 不 confirm（不扣款），客人重新下單 |

### 4.6 狀態（三種狀態分開記，計劃書 6.4-9）

- 付款：`UNPAID`（現金待付）／`PENDING`（LINE Pay 已發起）／`CONFIRMING`／`PAID`／`FAILED`／
  `CANCELLED`／`REFUNDED`／`PARTIALLY_REFUNDED`
- 同步：`NEW`（雲端）→ `IMPORTED`（POS 已收）→ `SETTLED`（POS 已成立銷售）／`VOIDED`
- 製作：**不追蹤**（沿用 docs/35 裁示；見 §10 Q1）

所有狀態轉換寫入 `online_order_events`（來源、時間、前後值）。

---

## 5. 介面合約（名稱為初稿）

### 5.1 客人 API（Worker，公開）

| 端點 | 說明 |
|------|------|
| `GET /api/menu` | 目前生效的菜單快照（ETag 快取） |
| `GET /api/tables/:code` | 解析桌位碼 → 桌名／內用外帶；無效碼 404 |
| `POST /api/orders` | 送單（`Idempotency-Key` 必填）；驗營業中、驗價、數量上限；回訂單權杖 |
| `GET /api/orders/:token` | 查本單狀態（只憑權杖，不回其他單） |
| `POST /api/orders/:token/linepay` | 發起／重新發起 LINE Pay（未付款的單才可） |
| `GET /api/linepay/confirm`、`/cancel` | LINE Pay 導回 |

### 5.2 POS 整合 API（Worker，只給店內 backend）

驗證（O3 實作定案）：標頭 `X-LuCamp-Timestamp`（秒）、`X-LuCamp-Nonce`（16–64 個英數或連字號）、
`X-LuCamp-Signature = hex(HMAC-SHA256(secret, METHOD \n PATH(含 query) \n TIMESTAMP \n NONCE \n hex(SHA-256(body))))`；
時間差超過 5 分鐘拒收、nonce 在 D1 記 10 分鐘防重放。兩邊用同一組跨語言測試向量守住
（`backend/tests/test_onlineorder_signing.py`、`online-order/test/signature-vector.test.ts`）。
secret 放 Worker secrets 和 backend `.env`，不進 repo。**一組雲端只服務一家店**（`ONLINE_ORDER_STORE_ID`），
別家店的店長不能發佈或重發 QR。桌位碼推送帶 `revision`（毫秒），雲端只收較新的——晚到的舊推送不能把停用的 QR 推回來。

| 端點 | 說明 |
|------|------|
| `PUT /integration/menu` | 發佈菜單快照 |
| `PUT /integration/menu/availability` | 帶菜單版本與遞增 revision 的完整可售狀態覆蓋（售完／恢復） |
| `PUT /integration/photos/:hash` | 上傳照片（已存在則 204） |
| `GET /integration/orders?after=<cursor>` | 拉新單／狀態有變的單（游標分頁，只掃索引） |
| `POST /integration/orders/:id/status` | 回報 IMPORTED／PAID／SETTLED／CANCELLED／REFUNDED（冪等） |
| `PUT /integration/store-status` | 營業中／暫停接單 |

POS 每次拉單同時當作心跳：Worker 記下 `last_pos_seen_at`。

### 5.3 店內 backend（FastAPI，新模組 `onlineorder`）

依 CLAUDE.md §2 分層；跨模組只呼叫 `menu`、`sales` 的 service。端點都要有 `response_model`＋
`operation_id`，前端用生成的 client。

- 背景工作：每 5 秒拉單（營業中）、重試佇列、補查付款。
- `GET /online-orders`（今日清單＋狀態）、`POST /online-orders/:id/load-to-cart`（現金單）、
  `POST /online-orders/:id/cancel`、`GET/POST` 暫停接單、`POST /menu/publish`。

---

## 6. 資料（D1，雲端）

```
stores_meta(store_id, accepting_orders, last_pos_seen_at, menu_version)
menu_snapshots(store_id, version, json, published_at)              -- 保留最近 N 版
tables(store_id, code, label, service_mode, active, rotated_at)
orders(id[ULID], store_id, token_hash, idem_key UNIQUE(store_id,idem_key), table_label, service_mode,
       menu_version, total, payment_method, payment_status, sync_status, invoice_carrier/buyer_id/donate_code,
       note, created_at, updated_at, row_version)
order_lines(order_id, menu_item_id, name, options_json, unit_price, qty, line_total)
linepay_payments(order_id UNIQUE, transaction_id TEXT, status, amount, request_at, confirmed_at, raw)
order_events(id, order_id, kind, from, to, source, at)
```

- 每張表都帶 `store_id`（CLAUDE.md §4）。
- 訂單權杖只存雜湊。
- 金額整數（INTEGER），時間 UTC 存、台北時區顯示與切營業日。
- D1 migration 用 `wrangler d1 migrations`，跟 Alembic 一樣進 repo。

POS 端（Postgres）新增 `online_orders`（遠端 ID 唯一、狀態、明細快照、對應 sale_id）、
`online_sync_outbox`（待回報佇列）、`sales.online_order_id`（唯一、可空）、
`sale_tenders` 的 LINE Pay 增加 channel（OFFLINE／ONLINE）與 transactionId 欄位（沿用 `linepay_transactions`，
加 `channel` 欄）。
另有 `stock_reservations`、`menu_items.stock_qty`／`menu_options.stock_qty`、
`catalog_products.online_enabled`（§3.7）。D1 另需 `order_holds`（HELD 結果）、`rate_counters`、`integration_nonces`（§8）。

---

## 7. 營運守衛

- **暫停接單**：POS 按鈕；或 Worker 發現 `last_pos_seen_at` 超過 2 分鐘 → 自動視為暫停，
  點餐頁顯示「線上點餐暫停，請至櫃台點餐」。已付款的 LINE Pay 單不受影響（POS 恢復後補拉）。
- **營業時間**：隨菜單發佈；非營業時間不接單。
- **POS 畫面**：線上訂單清單顯示連線狀態、最後同步時間、新單提示音
  （瀏覽器需店員先點一下啟用聲音，畫面要講明）。
- **用量**：POS 顯示 Workers／D1 今日用量；達 70% 提醒。

---

## 8. 資安（網址公開在桌上，一定會被亂打）

原則：**客人端的每一個請求都當作可能是惡意的**。防線分四層，越外層越便宜；
真正的上限一律在 D1 精確計算，不靠「大概」的機制。

### 8.1 威脅與對策

| # | 威脅 | 對策 |
|---|------|------|
| T1 | **機器人／腳本狂送單，灌爆 POS** | ①送單與發起 LINE Pay 前必須通過 **Cloudflare Turnstile**（免費、通常無感，Worker 端驗證、token 只能用一次）②D1 精確上限（§8.2）③**未付款的 LINE Pay 單不出現在 POS 清單**，只有現金單和已付款單會讓店員看到 ④異常偵測自動暫停（§8.3） |
| T2 | 拍下桌上 QR，在店外亂下單 | 現金單沒付款就不製作、不扣庫存（只有短暫保留）；非營業時間不接單；桌位碼可在 POS 一鍵重發；每桌未付款單數上限 |
| T3 | 惡意佔用庫存（下單不付，把甜點保留光） | 保留有到期時間（LINE Pay 10 分、現金 30 分）；保留數量計入每裝置／每桌上限；POS 可一鍵取消某桌全部未付款單 |
| T4 | 竄改價格、數量、選項 | 價格只在伺服器端依菜單算；LINE Pay 金額由伺服器決定，confirm 時再比對；不合法選項 422 |
| T5 | 偽造付款成功（亂打 confirm 網址） | confirm 網址上的參數一律不信任：Worker 以自己存的 orderId 查單，向 LINE Pay 呼叫 confirm／check，只有 LINE Pay 回成功才算付款 |
| T6 | 偷看別人的訂單 | 訂單權杖 128 位元隨機、只存雜湊；沒有可遞增猜的編號 API；回應不含其他客人資料、不含載具全碼 |
| T7 | 偽造 POS 整合請求（假裝店內拉單、亂改狀態） | HMAC 簽章＋時間戳（±5 分）＋nonce 防重放（D1 存 10 分鐘）；整合 secret 只在 Worker secrets／backend `.env`；正式與測試不同 secret |
| T8 | XSS／注入 | 備註與所有字串只當純文字渲染；嚴格 CSP、`X-Frame-Options: DENY`；D1 一律參數化查詢；請求 JSON 以 schema 驗證，body 上限 16 KB |
| T9 | 打爆免費額度讓服務停擺（每日 10 萬次請求） | 靜態資產不計額度；POS 顯示用量、70% 提醒；額度用完＝線上點餐停、櫃台照常營業（POS 完全不受影響）；必要時升級 US$5/月。綁正式網域後加開 WAF 速率限制規則（免費 1 條、依 IP、10 秒區間）與 Bot Fight Mode |
| T10 | 上傳惡意圖片 | 只有店內 POS 的 MANAGER 能上傳；檢查格式與大小；**一律重新編碼**成 WebP（丟掉原檔的任何夾帶內容與 EXIF） |
| T11 | 外洩密鑰 | secret 不進前端、不進 repo、不進 log；log 不記權杖、載具、統編 |

### 8.2 D1 精確上限（數字放 POS 設定、隨菜單發佈）

| 上限 | 預設 | 超過時 |
|------|------|-------|
| 同一裝置（cookie）未付款單 | 2 | 「您還有未付款的訂單」並導向該單 |
| 同一 IP 未付款單 | 4（同店 Wi-Fi 的客人共用 IP，不能太低） | 拒絕，請至櫃台 |
| 同一桌未付款單 | 4 | 拒絕，請至櫃台 |
| 全店未付款**現金**單 | 15 | 暫時只開放 LINE Pay（先付款才成立） |
| 每單 | 20 行、每行 10 份、總計 50 份 | 422 |
| 同一 IP 每分鐘送單／發起付款 | 5 | 429 |

另外使用 Workers 的速率限制 binding 當第一層（只能大概計數、各機房分開算）；它在免費方案是否可用，
實作時實測，**不可用也不影響**，因為真正的上限在 D1。

### 8.3 異常自動暫停

5 分鐘內新增未付款現金單超過 10 張（可設定）→ 雲端自動暫停接單，POS 跳出明顯提示
「線上點餐疑似被大量送單，已自動暫停」，店員看過後一鍵恢復。

### 8.4 資安測試（staging 必跑，結果寫進驗收紀錄）

灌單腳本（無 Turnstile、重放 Turnstile token、超過各項上限）、竄改價格／選項、偽造 confirm、
猜訂單權杖、偽造／重放 HMAC、超大 body、XSS 備註、保留洗光甜點——每項都要看到被擋下、
POS 清單沒被灌爆。

### 8.5 其他

- **測試與正式完全分開**：staging Worker＋staging D1＋LINE Pay 沙盒；production 另一組。
- 不收任何個資。載具／統編只用於開發票。

---

## 9. 實作波次（每波：TDD → 四道門 → Codex 審 → 停下讓店主確認）

| 波 | 內容 | 可單獨上線的成果 |
|----|------|----------------|
| O1 | 彈性菜單後端：分類、選項群組、計價、`sale_lines` 選項快照、照片上傳、餐飲／選項數量與保留 | — |
| O2 | POS 選項彈窗＋菜單管理頁（群組、照片）＋收據／出餐單／客顯印選項 | **POS 就能用新菜單** |
| O3 | `online-order/` 骨架：Worker＋D1＋R2＋菜單發佈（含零售商品、可售數量）＋客人菜單頁（只能看）＋安全標頭 | **電子菜單**（staging） |
| O4 | 送單（現金）＋Turnstile＋D1 上限＋庫存確認（HELD）＋POS 線上訂單清單＋帶入結帳＋回報＋異常自動暫停 | 現金線上點餐 |
| O5 | LINE Pay 線上付款（Worker）＋POS 自動成立銷售／開發票／出餐單 | 完整線上點餐 |
| O6 | 退款對稱、線上付款對帳、暫停接單、用量提醒、D1 每日備份到 R2、§8.4 資安測試 | — |
| O7 | 店內實測（iPhone／Android）、試營運 3 天、買網域、綁定、印桌牌 | 正式上線 |

O4/O5 涉及金流 → `/codex:adversarial-review`。UI 波次都要跑瀏覽器煙霧＋截圖。

---

## 10. 裁示紀錄與待辦

- Q1–Q5 已於 2026-10-01 裁示（§1）。
- **待店主**：向 LINE Pay 確認正式環境線上收款資格與費率。
- **待店主（O3 前）**：提供一組有 Workers Scripts、D1、R2 編輯權限的 Cloudflare API token
  （現有 `.env.r2` 的 token 可讀寫 R2、能列 Workers，但**沒有 D1 權限**）。
- §3.8「線上單一律原價」為預設，店主若要改再議。
