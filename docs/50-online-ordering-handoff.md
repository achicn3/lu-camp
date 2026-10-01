# 50 — 線上點餐／彈性菜單：交接文件

> **給接手的人（或 AI）**：這份是「目前做到哪、上線要注意什麼、接下來做什麼」的總整理。
> 細節以各編號規格為準；本文只指路。最後更新：2026-10-01。

## 1. 一句話現況

線上掃碼點餐的**店內前置工作**（彈性菜單、每日限量、餐飲退款、損耗與成本）已完成並在 `main`；
POS 選項點餐與菜單選項管理（O2）、菜單照片（O1d）也已完成。**雲端（Cloudflare）那一半還沒開始**；下一步是 O3 雲端骨架（需要店主的 Cloudflare token）。

## 2. 規格地圖

| 文件 | 內容 | 狀態 |
|------|------|------|
| docs/44 | 線上點餐總規格（Cloudflare Worker＋D1＋R2、POS 主動拉單、LINE Pay 線上付、資安、庫存同步） | 規格定案，雲端未做 |
| docs/47 | 餐飲交易紀錄頁＋餐點部分退款＋顧客螢幕點選同意 | **已完成** |
| docs/49 | 餐飲損耗與成本（客訴退款損耗、報廢報表、選項成本、關帳提醒） | **已完成** |
| docs/45、46、48、51、52、53 | MacBook 正式機升級說明（照**最新的 docs/53** 做即可，一次補齊） | 可用 |
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
| O2 | POS 分類分頁＋選項視窗（必選／最多選、加價、停售／售完選項不能點）；菜單頁選項群組管理、品項掛群組與介紹 | 純前端，煙霧 `menu-options-smoke.mjs` |

## 4. 正式環境上線注意（main 先上線時）

1. **照 docs/53 升級**（含六支 migration 與新後端套件，`uv sync` 不能跳過；打烊後做、先備份）。
2. 選項群組在菜單頁設定、POS 會跳選項視窗（O2）。**若要退回 O2 以前的版本**，先把品項的選項群組取消勾選，
   否則掛了必選群組的品項在舊版 POS 會被擋「要選○○」。
3. 報表的餐飲毛利已扣除損耗（報廢、盤點短少、客訴退款）；沒填成本的只列份數。
4. 每日限量預設全部「不限量」，行為與以前相同；勾了才需要每天填份數。
5. 「交易紀錄」的退貨對話框這版重構過（與餐飲頁共用），上線後建議試開一次（不必送出）。

## 5. 接下來的順序

| 順序 | 項目 | 說明 |
|------|------|------|
| ~~1~~ | ~~O2 POS 選項 UI＋菜單管理頁~~ | **已完成** |
| ~~2~~ | ~~O1d 照片~~ | **已完成**（推到 R2 留給 O3 的「發佈菜單」一起做） |
| 3 | **O3 雲端骨架** | `online-order/`（Cloudflare Worker＋D1＋R2）、菜單發佈、客人菜單頁（只能看）、安全標頭 |
| 4 | **O4 現金線上點餐** | 送單、Turnstile、D1 上限、庫存保留（HELD）、POS 線上訂單清單、異常自動暫停 |
| 5 | **O5 LINE Pay 線上付款** | request/confirm（沙盒已實測通過，docs/44 §4.4.1）、POS 自動成立銷售 |
| 6 | O6、O7 | 退款對稱、對帳、備份、資安測試；店內實測、試營運、買網域 |

## 6. 需要店主提供／確認的事

| 事項 | 何時需要 | 狀態 |
|------|---------|------|
| Cloudflare API token（Workers Scripts、D1、R2 **編輯**權限） | O3 開工前 | 未提供（現有 `.env.r2` 的 token 沒有 D1 權限、也不能建 token） |
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
- `test_passwords_are_hashed_not_stored` 約 4% 隨機紅（雜湊剛好含 "pw"），已裁示不修，單獨重跑確認即可。
