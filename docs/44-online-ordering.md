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

### 1.1 既有規則**不變**

- 餐飲（`line_type = MENU`）不扣庫存、不累點、不套門市活動、不可用購物金折抵（`lu-camp-menu-fnb` 裁示）。
  線上點餐的餐飲同樣受這些規則約束。
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
- 收據、出餐單、客顯在品名下方印出選項（例：`拿鐵　冰／燕麥奶 +20`）。

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
送單 → 雲端 order(payment=UNPAID) → POS 拉到「線上訂單」清單（響提示音＋數字徽章）
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
   Worker：驗價 → 建 order(payment=PENDING) ＋ payment row（orderId = 線上訂單 ID）
   → 呼叫 request（amount、packages、redirectUrls.confirmUrl/cancelUrl 指回 Worker）
   → 回 paymentUrl，手機跳 LINE Pay（App 或網頁）
② 客人在 LINE Pay 按付款 → 導回 confirmUrl
③ Worker：先查本地狀態（重複導回直接顯示結果）→ 呼叫 confirm（金額必須等於訂單金額）
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

驗證：`X-LuCamp-Timestamp` ＋ `X-LuCamp-Signature = HMAC-SHA256(secret, method+path+timestamp+body)`；
時間差超過 5 分鐘拒收、nonce 防重放。secret 放 Worker secrets 和 backend `.env`，不進 repo。

| 端點 | 說明 |
|------|------|
| `PUT /integration/menu` | 發佈菜單快照 |
| `PATCH /integration/menu/availability` | 售完／恢復 |
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

---

## 7. 營運守衛

- **暫停接單**：POS 按鈕；或 Worker 發現 `last_pos_seen_at` 超過 2 分鐘 → 自動視為暫停，
  點餐頁顯示「線上點餐暫停，請至櫃台點餐」。已付款的 LINE Pay 單不受影響（POS 恢復後補拉）。
- **營業時間**：隨菜單發佈；非營業時間不接單。
- **POS 畫面**：線上訂單清單顯示連線狀態、最後同步時間、新單提示音
  （瀏覽器需店員先點一下啟用聲音，畫面要講明）。
- **用量**：POS 顯示 Workers／D1 今日用量；達 70% 提醒。

---

## 8. 安全

- 客人只能：看菜單、送單、用自己的權杖查自己的單。不能碰 `/integration/*`。
- LINE Pay channel secret、整合 HMAC secret 只放 Worker secrets／backend `.env`。
- 速率限制：同一 IP 送單頻率上限（Cloudflare 免費方案的速率限制規則或 D1 計數，實作時擇一並實測）。
- 備註、品名一律轉義顯示；長度上限。
- **測試與正式完全分開**：staging Worker＋staging D1＋LINE Pay 沙盒；production 另一組。
- 不收任何個資。載具／統編只用於開發票，不寫 log。

---

## 9. 實作波次（每波：TDD → 四道門 → Codex 審 → 停下讓店主確認）

| 波 | 內容 | 可單獨上線的成果 |
|----|------|----------------|
| O1 | 彈性菜單後端：分類、選項群組、計價、`sale_lines` 選項快照、照片上傳 | — |
| O2 | POS 選項彈窗＋菜單管理頁（群組、照片）＋收據／出餐單／客顯印選項 | **POS 就能用新菜單** |
| O3 | `online-order/` 骨架：Worker＋D1＋R2＋菜單發佈＋客人菜單頁（只能看） | **電子菜單**（staging） |
| O4 | 送單（現金）＋POS 線上訂單清單＋帶入結帳＋回報 | 現金線上點餐 |
| O5 | LINE Pay 線上付款（Worker）＋POS 自動成立銷售／開發票／出餐單 | 完整線上點餐 |
| O6 | 退款對稱、線上付款對帳、暫停接單、用量提醒、D1 每日備份到 R2 | — |
| O7 | 店內實測（iPhone／Android）、試營運 3 天、買網域、綁定、印桌牌 | 正式上線 |

O4/O5 涉及金流 → `/codex:adversarial-review`。UI 波次都要跑瀏覽器煙霧＋截圖。

---

## 10. 待裁示

- **Q1 出餐狀態**：計劃書建議追蹤「製作中／可取餐」；現行裁示不追蹤。線上客人沒有狀態可看
  會不會一直跑來問？建議第一版**不追蹤**，訂單頁只顯示「已付款，餐點準備中」，試營運後再看。
- **Q2 LINE Pay 線上收款資格**：現有通路是「店內掃碼（Offline）」。線上收款可能需要另外向
  LINE Pay 申請或加開。O5 開工前要先用沙盒確認現有 channel 能不能呼叫 `/v4/payments/request`；
  正式環境需要店主向 LINE Pay 窗口確認。
- **Q3 LINE Pay 線上單是否自動成立銷售**：建議**自動**（錢已收，等店員按會拖慢出餐）；
  替代方案是放在清單等店員按「確認」。
- **Q4 咖啡豆、濾掛等零售商品**是否也要能線上點？那些有庫存，要做保留量；
  建議第一版**只開放餐飲菜單**。
- **Q5 現金單逾時**：預設 30 分鐘提醒、不自動取消，可以嗎？
