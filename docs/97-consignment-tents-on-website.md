# 97 — 寄售帳篷上官網（POS 維護、自動推送）

> 狀態：**已採納**（2026-10-30，店主八項裁示）。決策紀錄見 `docs/adr/ADR-033-consignment-tents-on-website.md`。
> 官網頁面已依 Claude Design「Lukeng Tents」實作於另一個 repo `~/lukeng-homepage`
> （分支 `feat/tents-page`，未部署），目前讀示範資料。本文件定義 POS 這一端與雲端的串接。

## 1. 目標與裁示

店長、店員在 POS 維護寄售帳篷的資料，官網 https://lukeng-camp.com/tents.html 自動跟著更新，
不必另外改網站。

**店主裁示（2026-10-30）：**

| # | 題目 | 裁示 |
|---|---|---|
| 1 | 怎麼送到官網 | **POS 自動推送**：存檔、結帳、退貨後自動更新，不用按「發佈」 |
| 2 | 哪些商品上官網 | **逐件勾「上架官網」**，沒勾的不出現 |
| 3 | 「已有人保留」 | POS 加 **「保留中」勾選**，只影響官網顯示，**不擋結帳** |
| 4 | 價格 | **顯示 POS 售價**（含稅 `listed_price`） |
| 5 | 只限寄售品 | 是（帳篷一律寄售） |
| 6 | 保留中的帳篷結帳 | **提醒但不擋**；結帳後保留自動失效 |
| 7 | 照片上限 | 每頂 8 張 |
| 8 | 售出後的照片 | **官網與雲端都刪，POS 留著**；退貨回庫照片自動回來（§4.4） |
| 9 | 對外網址 | **官網自己的網域**，不放在點餐網址底下（§2） |

## 2. 架構

```
POS（店內 MacBook）                     線上點餐雲端 order.lukeng-camp.com            官網 lukeng-camp.com
───────────────────                     ───────────────────────────────             ─────────────────────
寄售品＋官網資料 ──(HMAC 簽章 PUT)──▶  /integration/tent-photos/:hash → R2 tent-photos/     tents.html
  背景工作每分鐘比對內容雜湊             /integration/tents         → D1（最新一份）
  有變才推                                                                          
                                        GET /api/tents              ◀─┐  官網 Worker：lukeng-camp.com/api/tents
                                        GET /tent-photos/:hash.webp ◀─┘  與 /tent-photos/* 經內部連線轉過來
```

- **沿用線上點餐的雲端**（同一個 Worker、同一組整合密鑰、同一套簽章與防重放、同一個 R2 桶；
  帳篷照片放獨立的 `tent-photos/` 前綴，才能放心刪除而不碰到菜單照片）。
  雲端永遠不連進店內（ADR-027）。不另建第二個要維護的雲端服務。
- **對外網址用官網自己的網域**（店主裁示 9，2026-10-30）：`lukeng-camp.com/api/tents`、
  `lukeng-camp.com/tent-photos/<hash>.webp`。官網 Worker 只把這兩個路徑經 Cloudflare 內部連線
  （service binding，主機名 `tents.internal`）轉給線上點餐雲端，其餘仍是靜態檔。
  **點餐網址 order.lukeng-camp.com 上這兩條一律 404**，帳篷資料不會出現在點餐網域。
- 店裡電腦關機時，官網照樣顯示最後一次推送的清單。

## 3. POS 資料

放在 `onlineorder` 模組，比照「線上零售商品」（`OnlineRetailListing`，docs/63 §13）：
只存官網要用、POS 原本沒有的欄位；售價、品牌、狀態一律讀序號品本身，不重複存。
跨模組只經 `InventoryService`／`MenuService`（CLAUDE.md §2）。

### 3.1 新表 `online_tent_listings`

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | PK | |
| `store_id` | FK stores | CLAUDE.md §4 |
| `serialized_item_id` | FK serialized_items | 唯一（同店同一件只有一筆） |
| `published` | bool，預設 false | 「上架官網」 |
| `reserved` | bool，預設 false | 「保留中」，只影響官網 |
| `color` | varchar(30)，可空 | 帳篷顏色（文字） |
| `nights` | int，可空，CHECK ≥ 0 | 使用過幾露；0＝未使用；空＝不顯示 |
| `missing` | text，可空 | 缺件，一行一項 |
| `defects` | text，可空 | 瑕疵，一行一項 |
| `extras` | text，可空 | 額外配件，一行一項 |
| `video_url` | varchar(300)，可空 | YouTube 網址；存檔時驗證能解析出影片 id |
| `created_at`／`updated_at` | | `created_at` 當作「上架日」 |

缺件／瑕疵／配件：每項最多 100 字、最多 20 項；存檔時去掉空行。

### 3.2 新表 `online_tent_listing_photos`

`listing_id`、`position`（0 起算，0 是封面）、`photo_sha256`（FK 到 `menu_photos`，沿用菜單照片表：
同一家店、依內容雜湊去重、已會轉成 webp）。每頂最多 **8 張**。

### 3.3 可以上架的商品

- 只限 **寄售序號品**（`ownership_type = CONSIGNMENT`）。帳篷依規定一律寄售。
- 編輯、勾上架只限 **在庫**（`IN_STOCK`）的商品；售出後資料唯讀。

## 4. 推送到官網的內容

### 4.1 誰會出現在清單

| 序號品狀態 | 「上架官網」有勾 | 官網顯示 |
|---|---|---|
| IN_STOCK，沒勾保留 | ✓ | 寄售中 |
| IN_STOCK，有勾保留 | ✓ | 已有人保留 |
| SOLD，售出 30 天內 | ✓ | 最近售出 |
| SOLD 超過 30 天、還給寄售人、報廢 | — | 不顯示 |
| 任何狀態 | ✗ | 不顯示 |

退貨讓商品回到 IN_STOCK → 自動變回「寄售中」。

### 4.2 欄位對照（JSON 格式與官網 `tents-data` 示範檔相同）

| JSON | 來源 |
|---|---|
| `id` | `serialized_items.item_code`（客人詢問時報的編號） |
| `brand` | 序號品品牌名稱；沒有品牌＝`null` |
| `name` | `serialized_items.name` |
| `color`／`nights` | 官網資料 |
| `missing`／`defects`／`extras` | 官網資料，拆成陣列 |
| `photos` | 照片雜湊陣列（**售出的一律空陣列**，§4.4）；雲端回應時轉成相對網址 `/tent-photos/<hash>.webp`（掛在官網網域下） |
| `video` | `video_url` |
| `price` | `listed_price`（含稅，整數元） |
| `status` | 依 §4.1：`available`／`reserved`／`sold` |
| `listedAt` | 官網資料建立日 |
| `updatedAt` | 官網資料、序號品兩者 `updated_at` 較晚者；售出則為 `sold_date` |
| `meta.syncedAt` | 這份內容推送的時間 |
| `meta.soldDays` | 30 |
| `meta.demo` | 不送（官網上的「示範」字樣自動消失） |

**絕不送出**：寄售人、成本、抽成、備註等內部資料（CLAUDE.md §5）。只送上表欄位。

### 4.3 什麼時候推

- 沿用線上點餐背景工作（`onlineorder/scheduler.py`）。每 **60 秒**組一次清單內容、算雜湊，
  **跟上次推成功的不同才推**。所以存檔、結帳、退貨後，官網約 1 分鐘內更新；
  不必去改銷售、退貨、寄售的程式。
- 店員在 POS 存檔後立刻試推一次，畫面直接顯示「已更新到官網」或錯誤原因。
- 順序：直接推清單；雲端若回 409 `missing_photo`（附缺的雜湊），就把那幾張推上去
  （`/integration/tent-photos/:hash`）再推一次清單。POS 不另記哪些照片推過——雲端有沒有才算數，
  照片被雲端刪掉後（§4.4）重新上架也會自動補推。中途失敗下一輪重試；雲端冪等。
- 新表 `online_tent_publications`（`store_id`、`cloud`、`content_sha256`、`version`、`pushed_at`、
  `last_error`）記錄推送結果，POS 畫面顯示上次同步時間與錯誤。
- 雲端沒設定（網址或密鑰空白）時什麼都不做，POS 頁面提示「尚未設定」。

### 4.4 售出後刪照片（裁示 8）

- 售出的帳篷在推送內容裡 `photos` 一律是空陣列；官網「最近售出」與詳情頁只顯示色塊與「已售出」，不顯示照片。
- 雲端每收到一份清單，就把 R2 `tent-photos/` 底下**不再被清單引用**的照片刪掉——售出、下架、還給寄售人的都一樣。
- POS 的 `online_tent_listing_photos` 不刪。退貨讓商品回到 IN_STOCK 時，照片重新出現在清單，
  雲端回 `missing_photo`，POS 自動補推。

## 5. 雲端（`online-order/`）

- `PUT /integration/tents`（簽章，上限 256 KB）：body＝`{version, syncedAt, soldDays, tents:[…]}`。
  `version` 只能往前（比照菜單），舊的回 409。存進 D1 新表 `tent_listing`（每店一列，存整份 JSON）。
  照片雜湊必須都已存在 R2 `tent-photos/`，否則回 409 `{"error":"missing_photo","missing":[…]}`，清單不更新。
  更新成功後刪掉 `tent-photos/` 底下沒被新清單引用的照片。
- `PUT /integration/tent-photos/:hash`（簽章）：存到 R2 `tent-photos/<hash>.webp`，驗證內容雜湊（比照菜單照片）。
- 只在主機名 `tents.internal`（官網 Worker 的內部連線）提供，點餐網址上 404：
  - `GET /tent-photos/:hash.webp`：讀 R2 `tent-photos/`；被刪掉的回 404。
  - `GET /api/tents`：回最新一份，照片轉成相對網址；`Cache-Control: public, max-age=60`。
    還沒推過回 `{meta:{…}, tents:[]}`。
- `wrangler.jsonc` 的 `run_worker_first` 要含 `/tent-photos/*`，否則會被靜態資產（點餐頁）接走。
- **同店一次只處理一份**（D1 `tent_lock`，期限 60 秒）：驗照片、寫清單、清照片整段持鎖；拿不到回 409 `busy`，
  POS 記下原因、下一輪再推。清照片時每刪一批前再確認鎖還是自己的（請求卡太久被接手就停手）。
- D1 migration `0017_tent_listing.sql`、`0018_tent_lock.sql`。

### 5.1 已接受的殘餘風險（店主裁示 2026-10-30）

- 保留中的帳篷在背景工作兩次檢查之間（約 1 分鐘）**賣掉又退貨**，保留勾選不會被清掉，官網仍顯示「已有人保留」。
  徹底解決要在結帳流程加程式，違反「不能影響 POS」；實務上幾乎不會發生，發生時店員在「官網寄售帳篷」取消勾選即可。

## 6. POS 畫面

寄售頁新增分頁「官網帳篷」，店長、店員都可以用：

- 清單：在庫的寄售品，欄位有編號、品牌、名稱、售價、上架官網、保留中、資料齊不齊
  （沒照片、沒填露數會標示）。最上方顯示「官網上次更新：10/30 14:20」或錯誤。
- POS 結帳掃到「保留中」的帳篷：跳提醒「這頂在官網標示保留中，確定要賣？」，可以照賣（裁示 6）；
  背景工作組清單時發現已售出的帳篷還勾著保留，就把勾選清掉（退貨回庫時不會又變成保留中）；
  一樣不改結帳流程。提醒的資料由 POS 掃碼查件時另呼叫 `GET /api/v1/online-order/tents/by-item-code/{item_code}` 取得。
- 編輯：顏色、使用露數、缺件／瑕疵／額外配件（多行文字框，一行一項）、YouTube 網址、
  照片（上傳、排序、刪除，第一張是封面），以及兩個勾選。
- 「上架官網」與「保留中」的變更寫 `audit_log`（誰、何時、哪一件、前後值）。

## 7. 官網（`~/lukeng-homepage`）

- 官網 Worker（`src/worker.js`）：`/api/tents`、`/tent-photos/*` 經 service binding `TENTS`
  （→ `lu-camp-online-order`）轉過去，只轉路徑、不帶客人的標頭；其他 `/api/*` 404；其餘靜態檔。
- `tents.js` 讀同網域的 `/api/tents`，拿掉 `site/api/tents.json` 示範檔。
- 售出的帳篷沒有照片：詳情頁不顯示「搭好全景／照片準備中」佔位格，只顯示色塊＋「已售出」。
- 部署順序：雲端（Worker＋D1 migration）→ POS 升級並至少上架一頂 → 官網。
  官網上線前清單不能是示範資料。

## 8. 實作波次

| 波次 | 內容 | 測試 |
|---|---|---|
| W1 | POS 後端：三張表＋migration、`TentListingService`（編輯、驗證、組清單、雜湊）、API | 單元＋整合（狀態對照、只限寄售、資料外洩白名單、雜湊不變不推） |
| W2 | 雲端：`/integration/tents`、`/api/tents`、D1 migration | vitest（簽章、版本往前、缺照片、點餐網址讀不到） |
| W3 | POS 推送：背景工作、存檔後試推、推送紀錄 | 整合（假雲端） |
| W4 | POS 畫面「官網帳篷」 | 前端測試＋瀏覽器煙霧 `frontend/scripts/tents-listing-smoke.mjs` |
| W5 | 官網接正式資料、部署、MacBook 升級說明（docs/98） | 實機確認 |

W1–W3 動到 migration 與共用背景工作，依序做（CLAUDE.md §10）。每波 Codex 審查。

## 9. 已確認（2026-10-30）

見 §1 裁示 5–8。
