# 50 — 線上點餐／彈性菜單：交接文件

> **給接手的人（或 AI）**：這份是「目前做到哪、上線要注意什麼、接下來做什麼」的總整理。
> 細節以各編號規格為準；本文只指路。最後更新：2026-10-06。

## 1. 一句話現況

線上掃碼點餐的**店內前置工作**（彈性菜單、每日限量、餐飲退款、損耗與成本）已完成並在 `main`；
POS 選項點餐與菜單選項管理（O2）、菜單照片（O1d）、**雲端電子菜單（O3，只能看）**已完成；測試版已部署。
O4 現金線上點餐：雲端送單（O4a）、雲端拉單／回報（O4b 雲端端）、**店內收單與 POS 線上訂單畫面（O4b 店內端、O4c）**已完成於分支；O4d 客人購物車／送單／查單及可售數量同步也已實作，仍未部署或合併。

### 2026-10-06 接手檢查

- 接手工作樹 `/home/test/lu-camp-o4`，分支 `feat/online-order-cash`；原第二輪審查因額度限制中止，並未通過審查或合併。
- 已重現並修正四項：LINE Pay 結果不明後補單遺漏 `online_order_id`；重送識別碼未區分線上單；
  多個回報程序以 `SKIP LOCKED` 越過同單前一筆；保留到期後仍顯示 `HELD`。
- 付款補單沿用原線上單；冪等指紋只在有線上單時新增欄位，維持既有一般銷售相容。
- 回報每輪每單只取最早待送的一筆，前筆被鎖住或等待重試時不得越過；不同訂單可各自處理。
- 保留到期按「訂單 → 保留」順序取鎖，加回份數後清除店內保留狀態並排入雲端回報。
  Worker 接受 `HELD → NONE`，仍保留未付款單可到櫃台結帳。
- 驗證：真 PostgreSQL 隔離測試庫上的線上單／客顯付款／回報並發 53 項，既有銷售／簽署／冪等並發
  53 項；Worker 113 項，POS 相關 18 項。Python 修改檔 lint／mypy、Worker 型別檢查通過。
- 已補付款保護：線上單的購物車進入 PROCESSING／PAYMENT_UNCERTAIN 時，取消、保留到期及另一櫃台付款均被擋下；確認未扣款後才能釋放份數。LINE Pay 結帳必須先提交開始結帳狀態。
- O4d：客人選項／數量／備註、購物車、現金送單、安全驗證與訂單狀態輪詢。送單前持久化識別碼；回應遺失後重整重試仍取得同一單。
- 可售狀態：POS 每 5 秒重取完整份數，先持久化版本再推送；Worker 僅覆蓋可售狀態，保留已發布的名稱與價格。晚到回覆不得確認較新版本；版本衝突會重取，已送達資料每 60 秒重新確認。客人菜單每 15 秒更新。
- 新 migration：Postgres `c6e7a9b2d4f1`；D1 `0004_availability.sql`。Worker 必須配置公開的 `TURNSTILE_SITE_KEY` 及私密的 `TURNSTILE_SECRET`；正式環境不可使用測試 key。CSP 允許 Cloudflare 驗證的 script/frame host。
- 最後驗證：前端完整 1,005 項、Worker 129 項通過；後端完整執行 2,472 項通過、2 項守衛失敗（字型缺字／新增指紋欄位清單），修正後相關 67 項重測全過；該完整執行覆蓋率 90.17%。全後端 mypy 449 檔、API 合約生成無漂移。
- 真 Worker/D1＋backend/Postgres 煙霧：POS 收單到現金結帳／雲端 PAID、取消與接單開關；客人手機頁送單到 POS 保留、取消後輪詢、店內售完同步。另有回應遺失後重整重試的 Worker 煙霧，使用 Cloudflare 官方測試驗證，不代替正式環境驗證。
- 截圖：`~/tmp/lu-camp-shots/online-guest-pos/`、`online-orders-pos/`、`online-cash/`。新腳本 `frontend/scripts/online-guest-pos-smoke.mjs` 與 `online-order/test/guest-cash-smoke.mjs`。
- **未部署、未合併**。協作審查遇到模型額度中止；合併前仍需完成獨立金流／併發審查、與最新版 main 整合及合併關卡。O5 線上 LINE Pay 尚未實作。

## 2. 規格地圖

| 文件 | 內容 | 狀態 |
|------|------|------|
| docs/44 | 線上點餐總規格（Cloudflare Worker＋D1＋R2、POS 主動拉單、LINE Pay 線上付、資安、庫存同步） | 規格定案，雲端未做 |
| docs/47 | 餐飲交易紀錄頁＋餐點部分退款＋顧客螢幕點選同意 | **已完成** |
| docs/49 | 餐飲損耗與成本（客訴退款損耗、報廢報表、選項成本、關帳提醒） | **已完成** |
| docs/45、46、48、51、52、54、55 | MacBook 正式機升級說明（照**最新的 docs/55** 做即可，一次補齊；docs/53 是另一條線的發票升級） | 可用 |
| ADR-027 | 線上點餐放 Cloudflare、POS 主動拉單 | 提議中 |

## 3. 已在 main 的功能

| 波 | 功能 | 重點 |
|----|------|------|
| O1a | 分類、可共用選項群組、選項加價、品項介紹 | migration `0a577011b3c1` |
| O1b | 結帳帶選項：後端計價、選項寫進品名（撞名才帶群組名）、`menu_options_snapshot` | 同上 migration |
| O1c | 每日限量：每天歸零、開店檢查填份數、改成／±1（−1 選報廢／盤點校正）、POS 剩幾份／售完、原子扣減、作廢依版本號加回 | migration `b86c4571decf` |
| O1e | 餐飲交易紀錄 `/fnb-sales`、餐點部分退款（只退外部付款、點數只沖二手、可勾還能賣）、純餐點退款顧客螢幕點選同意 | migration `3c59ce725956` |
| O1f | 選項成本、報廢／盤點短少凍結成本、客訴退款成本算損耗、毛利報表「餐飲損耗」、關帳提醒剩餘份數 | migrations `31f4b20c0acc`、`edaa5e5ce783` |
| O1d | 菜單照片：上傳轉 WebP、縮 1200、去 EXIF／XMP（含 GPS）、內容雜湊去重**存資料庫**（店主 2026-10-02 裁示，備份自動涵蓋）；公開讀取 `GET /menu-photos/{sha256}.webp`（不需登入）；新套件 pillow、pillow-heif | migration `2e52d783ec0e`，煙霧 `menu-photo-smoke.mjs` |
| O3 | 雲端 `online-order/`（Worker＋D1＋R2）：店內 HMAC 簽章（防重放／竄改／過期、一組雲端只服務一家店）、菜單快照（不含成本）、照片與字型子集只推一次、桌位碼（依設定桌號、可單桌重發、版本號防晚到）、客人電子菜單頁（B1 夜墨金＋辰宇落雁體、開場 logo、時段問候、只能看） | migration `da91ffee580d`；D1 `0001`、`0002`；煙霧 `online-menu-smoke.mjs` |
| O4 | 雲端送單（伺服器驗價、冪等、Turnstile、未付款上限、POS 離線即暫停）；POS 每 5 秒拉單（兼心跳）、限量品項拉到時直接扣份數＝保留（不夠就 REJECTED）、現金單 30 分鐘沒來付加回份數（單子不取消）；回報走持久化佇列（退避重試、明確拒收不重試）；POS「線上訂單」（徽章＋提示音、帶入結帳以 POS 現價重算並提示差額、取消、暫停／恢復接單）；結帳帶 `online_order_id`：交易內先加回保留再照常扣，`online_orders.sale_id` 唯一＝一張線上單只成立一筆銷售 | migration `4f1c8e2a9b70`；D1 `0003`；升級 docs/57；煙霧 `online-orders-pos-smoke.mjs` |
| O2 | POS 分類分頁＋選項視窗（必選／最多選、加價、停售／售完選項不能點）；菜單頁選項群組管理、品項掛群組與介紹 | 純前端，煙霧 `menu-options-smoke.mjs` |

## 4. 正式環境上線注意（main 先上線時）

1. **照 docs/55 升級**（含本系列 migration 與新後端套件，`uv sync` 不能跳過；打烊後做、先備份）。正式機先不設定線上點餐雲端。
2. 選項群組在菜單頁設定、POS 會跳選項視窗（O2）。**若要退回 O2 以前的版本**，先把品項的選項群組取消勾選，
   否則掛了必選群組的品項在舊版 POS 會被擋「要選○○」。
3. 報表的餐飲毛利已扣除損耗（報廢、盤點短少、客訴退款）；沒填成本的只列份數。
4. 每日限量預設全部「不限量」，行為與以前相同；勾了才需要每天填份數。
5. 「交易紀錄」的退貨對話框這版重構過（與餐飲頁共用），上線後建議試開一次（不必送出）。

## 5. 接下來的順序

2026-10-06 新增菜單體驗需求見 [docs/58](./58-online-menu-experience-plan.md)。順序調整為：
**O4 穩定化與審查 → M1a 後台呈現設定 → M1b 手機首頁與菜單 → M1c 手沖體驗／餐飲加購 → M1d 零售商品整合 → O5 → M2 推薦與分析**。
M1a 已於 2026-10-07 實作在 `feat/online-menu-presentation`（未合併／部署），migration `d8f2a4c6e901`；M1b–M1d 待實作。資料層／POS／結帳／報表必須與 UI 同波驗收。人氣榜與分析不擋第一階段上線。

| 順序 | 項目 | 說明 |
|------|------|------|
| ~~1~~ | ~~O2 POS 選項 UI＋菜單管理頁~~ | **已完成** |
| ~~2~~ | ~~O1d 照片~~ | **已完成**（推到 R2 留給 O3 的「發佈菜單」一起做） |
| ~~3~~ | ~~O3 雲端骨架＋電子菜單~~ | **已完成**；零售商品上線（§3.7）與售完即時同步延到 O4 |
| 4 | **O4 現金線上點餐** | 雲端端與店內收單、POS 畫面已完成；O4d 客人點餐畫面及可售數量同步已實作於分支；待獨立審查與整合合併 |
| 5 | **O5 LINE Pay 線上付款** | request/confirm（沙盒已實測通過，docs/44 §4.4.1）、POS 自動成立銷售 |
| 6 | O6、O7 | 退款對稱、對帳、備份、資安測試；店內實測、試營運、買網域 |

## 6. 需要店主提供／確認的事

| 事項 | 何時需要 | 狀態 |
|------|---------|------|
| Cloudflare API token（Workers Scripts、D1、R2 **編輯**權限） | O3 開工前 | **已提供**：`/home/test/lu-camp/.env.cloudflare-online-order`（帳號層級 token；`/user/tokens/verify` 會回 Invalid，屬正常） |
| LINE Pay **正式**線上收款開通 | O5 上線前 | 店主確認可以，手續費 2.2% |
| 網域 | O7 | 未買，先用 workers.dev |
| 餐點退款「點選同意」是否符合記帳士見解 | 隨時 | 建議店主確認（作業要點第 9 點，docs/47 §1） |

## 7. 開發環境與驗證

- 工作樹：`/home/test/lu-camp-online-order`（不要在主工作樹開發；每個工作樹自己的資料庫）。
- 後端測試：`DATABASE_URL=…/lucamp_pytest`（base 庫必須存在；pytest 會自建 `<base>_test_<pid>` 臨時庫，可併跑）。
- 瀏覽器煙霧（docs/20）：本段用 `lucamp_o1c_e2e` 庫、後端 :8100、前端 :3100。新增的煙霧腳本：
  `menu-daily-stock-smoke.mjs`、`fnb-refund-smoke.mjs`、`fnb-tap-consent-smoke.mjs`（會暫時打開電子發票並還原）、
  `cash-leftover-waste-smoke.mjs`（會關掉班別再開一個新的）、`menu-options-smoke.mjs`（每跑一次多建兩個選項群組）。
- Codex 審查一律 `--base origin/main`，金流／簽署相關用 `adversarial-review`。

### 7.1 雲端測試版（staging）

- 網址 `https://lu-camp-online-order-staging.noiping2.workers.dev`；D1 `lu-camp-online-order-staging`（APAC）、R2 同名。
- 部署：`cd online-order && set -a; . /home/test/lu-camp/.env.cloudflare-online-order; set +a && npx wrangler deploy`
  （會先 build 客人頁）；D1 變更：`npx wrangler d1 migrations apply lu-camp-online-order-staging --remote`。
- 整合密鑰：Worker 端 `wrangler secret put INTEGRATION_SECRET`；店內端在 `/home/test/lu-camp/.env.online-order-staging`
  （`ONLINE_ORDER_BASE_URL`／`ONLINE_ORDER_SECRET`／`ONLINE_ORDER_STORE_ID`，權限 600，不進 repo）。
- 示範菜單從本機示範庫 `lucamp_staging_demo` 發佈（不碰正式資料）。正式版另建 Worker／D1／密鑰（§8.5）。
- 本機開發：`cd online-order && pnpm dev`（`.dev.vars` 放本機密鑰）；Worker 測試 `pnpm test`、型別 `pnpm typecheck`。
- 套件：pnpm 11 有「新版本發佈未滿一段時間不裝」的保護，**不要繞過**（剛發佈的 wrangler 版本先別用）。

## 8. 踩過的坑（接手前先看）

- **`ruff format` 不要對整個 app/tests 跑**：main 本來就不是 format-clean，會重排近百個無關檔。只 format 自己改的檔。
- **`pgrep`/`pkill -f` 會比對到自己那條指令**：樣式寫成 `[l]u-camp-…`、`[u]vicorn …`。
- **`cmd | tail -1 && 下一步`**：結束碼被 tail 吃掉，前面紅了後面照跑。檢查要單獨跑。
- **openapi-typescript 會把有預設值的 request 欄位當必填**：新增選填欄位用 `X | None = None`，否則既有前端編譯不過。
- **同一個資料庫交易內 `now()` 都相同**：用時間比先後會失準（O1c 作廢加回改用版本號 `stock_generation`）。
- **加 optional 欄位會打破冪等指紋**：沒值時不放進指紋（`menu_option_ids`、`resellable` 都這樣做，並有舊式 sha256 比對測試）。
- **結帳失敗會回滾整個測試交易**：整合測試裡「失敗的結帳」要放最後一步，否則後面呼叫會 401。
- **`/sales/fnb` 這類固定路徑要排在 `/{sale_id}` 前面**，否則被當單號 422。
- **還原演練涵蓋率守衛**（`test_restore_coverage_drift`）：新增資料表必須加進 `restore_drill.py` 的檢查或明寫豁免。
- **測試假資料要符合 OpenAPI 型別**：O2 起 POS／菜單頁會讀 `option_groups`，舊測試的菜單假資料缺這欄會整頁炸掉，已補 `option_groups: []`。
- **照片上傳已知風險（Codex O1d 第四輪，待店主裁示）**：轉檔一次一張、排隊中的上傳仍佔著一條資料庫連線
  （驗登入時開的交易）。要同時十五張以上才會吃光連線池卡住 POS；只有店長會傳照片，判定實務上不會發生。
  若之後有多人同時傳照片的情境，再改成轉檔前先結束交易。
- **從備份還原後要重發 QR（已知風險，Codex O3 第三輪，待店主裁示）**：若備份之後有「重發 QR」，還原會把
  舊碼帶回本機，下次發佈會讓被停用的舊 QR 復活。還原後到菜單頁「線上點餐」把重發過的桌**再重發一次**
  （或全部重發並重印）。要徹底防住得做本機與雲端的桌位碼對帳，成本高，先以作業流程處理。
- **客人頁的 `hidden` 會被 class 的 display 蓋掉**：隱藏中的詳情視窗曾透明地擋住整頁（只有真瀏覽器抓得到）；
  `public/app.css` 已加 `[hidden]{display:none!important}`。
- **客人頁的中文字要在字型子集的 `UI_TEXT` 裡**：漏了會掉回系統字型；`test_onlineorder_font.py` 會掃客人頁原始碼，
  改文案後紅了就補字。
- **辰宇落雁體有保留字型名稱**：子集一律改名 `LukengHand`，不可改回原名（OFL）。
- `test_passwords_are_hashed_not_stored` 約 4% 隨機紅（雜湊剛好含 "pw"），已裁示不修，單獨重跑確認即可。


### 2026-10-07：M1b 與餐飲管理分組（功能分支，未部署）

`feat/online-menu-home` 接續 M1a：客人首頁三個方向入口、最多三項人工推薦、卡片加入與行動版返回流程；`/menu` 改為品項、選項群組、分類與排序、線上發布四個分頁，增加搜尋與篩選且保留草稿。詳見 [docs/58 §10](58-online-menu-experience-plan.md#10-m1b-與餐飲管理-ux2026-10-07)。未改庫存／金額／發票流程，M1c 手沖體驗與加購仍待實作。
