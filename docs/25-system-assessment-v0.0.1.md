# v0.0.1 系統實跑與優化評估

> 評估日期：2026-07-13（Asia/Taipei）
> 程式基線：`main` / `df603fbb445062f80112bdef714da048014fa913`
> 版本標籤：本機 annotated tag `v0.0.1`（尚未推送遠端）
> 評估原則：以真 backend、真 PostgreSQL、真瀏覽器與 Fake hardware-agent 實跑；本階段只記錄與安排，不直接修正程式碼。

## 1. 結論摘要

系統的核心門市流程已具備可操作性，且自動測試基礎明顯高於一般原型：後端 965 tests、前端 294 tests、hardware-agent 148 tests 均通過；API 合約沒有漂移；13 個主要頁面的瀏覽器走查沒有 page error、console error 或未中文化狀態字串。登入、開帳、會員建檔、收購、購物金、寄售、POS、寄售付款、採購、盤點、活動與報表都已透過 UI 實際操作。

目前不建議直接把 `v0.0.1` 視為可上正式環境的 release。主要原因不是核心功能不能用，而是存在下列 release blockers：

1. 三個服務的 lockfile 都含有已知漏洞，其中包含 Starlette 與 cryptography 的 high-severity advisory。
2. 門市操作維持「session 不因時間自動登出」的產品決策；目前的實作是「無 `exp` bearer token + localStorage」，已有逐請求 DB 覆核，但仍缺每台裝置可撤銷 session、憑證輪替與瀏覽器儲存防護。
3. 根目錄一般開發資料庫 `lucamp` 落後 Alembic head 11 個 migration；目前實跑服務其實使用另一個已在 head 的 `lucamp_e2e`。
4. 專案宣稱的本機品質關卡目前無法全綠：入口腳本有執行／Python 名稱問題，且後端與 hardware-agent 共 50 個檔案未通過 formatter。
5. 390px viewport 實測頁面寬度被頂部導覽撐到 816px，行動裝置會水平溢出。
6. UI QA 的身分證防呆與「120 天大表」出現假通過：截圖顯示腳本按到會員搜尋而非建檔送出，且腳本沒有建立或核對 120 天資料。

建議先完成本文件「P0：release blocker」再考慮推送 `v0.0.1` 或部署正式資料。

## 2. 實際環境與操作範圍

### 2.1 環境

- PostgreSQL 16.10：Docker，`127.0.0.1:1234`，health check 正常。
- Backend：FastAPI / Uvicorn，`http://localhost:8000`；`GET /api/v1/health` 回 200。
- Frontend：Next.js 16.2.7 dev server，`http://localhost:3000`；首頁回 200。
- Hardware-agent：測試期間在 `127.0.0.1:8001` 以全 Fake 裝置啟動，完成列印與開錢櫃呼叫後已停止。
- 實跑 backend 的資料庫：`lucamp_e2e`，Alembic 在 `c1d2e3f4a5b6 (head)`；未接觸正式資料。
- 瀏覽器：Playwright Chromium，桌面 1366×1000；另以 390×844 檢查窄螢幕。

### 2.2 親自走過的主要流程

1. 管理者登入與受保護路由導覽。
2. 現金班別開帳／共用班別檢視。
3. 建立同時具會員、賣方、寄售人角色的聯絡人；另曾嘗試錯誤身分證檢核碼，但追加截圖複核確認 QA 按錯送出按鈕，此防呆需重新實跑驗證。
4. 買斷收購：新建品牌／型號／分類、現金撥款、取得序號、列印標籤。
5. 購物金收購：購物金溢價、低消門檻、餘額與防呆。
6. 寄售入庫、寄售品銷售、待付款結算、現金支付寄售款。
7. POS：序號品＋餐飲同車、會員歸戶、現金／購物金／混合付款、找零、結帳。
8. 庫存清單與補印標籤。
9. 採購：供應商、採購單、分批收貨、發票資料、草稿與取消。
10. 盤點：建單、實點、差異、二次確認、確認後唯讀。
11. 門市活動：建立、啟用、結束。
12. 報表：今日營運、趨勢、現金對帳、銷售毛利、庫存價值、寄售應付、季粒度；但本次留存截圖只有「今日營運」，沒有足以證明 120 天趨勢或季粒度資料量的畫面。
13. 兩個獨立瀏覽器同時開 POS／收購並檢視同一現金班別。

瀏覽器全路由掃描結果：13 routes、0 pageerror、0 console.error、0 未中文化狀態術語。截圖與 JSON 證據在本次工作環境的 `/tmp/lu-camp-v0.0.1-ui/`；完整流程截圖在 `/tmp/lu-camp-v0.0.1-full-e2e/`。

### 2.3 追加截圖全檢視（2026-07-14）

- 來源：`/home/test/tmp/lu-camp-review/`，共 29 張 PNG 與 1 份 `ui-sweep-summary.json`。
- 已逐張檢視：22 張桌面主要流程、1 張 CLERK 權限畫面、6 張窄螢幕畫面。
- 桌面庫存與寄售付款畫面確實有 20 列資料，寄售售出日期橫跨 2026/3–2026/7；這可證明列表在一頁 20 列下可讀，但不能推論 120 天報表查詢或大量資料效能已驗證。
- 報表截圖只有 2026/7/13 的「今日營運」，畫面為 0 筆交易；沒有趨勢圖、季粒度或長區間載入截圖。
- 6 張窄螢幕截圖的實際圖片寬度皆為 816px，與 390px viewport 的 `scrollWidth = 816` 一致，確認不是單一路由問題。
- 下列結論只採截圖可直接支持的事實；JSON 中「120 天資料」與「身分證防呆通過」不再視為有效證據。

## 3. 驗證結果

| 項目 | 結果 | 證據／備註 |
|---|---|---|
| `./check.sh` | 失敗 | 檔案 mode 為 `644`，直接執行得到 `Permission denied`。 |
| `bash check.sh` | 失敗 | 預設 `python -m uv`，環境只有 `python3` 與 `uv`，得到 `python: command not found`。 |
| `UV=uv bash check.sh` | 失敗 | backend lint 通過，但 `ruff format --check` 指出 43 個檔案需格式化，腳本提前停止。 |
| Backend mypy | 通過 | 296 source files，0 issue。 |
| Backend pytest | 通過 | 965 passed；93.05% overall coverage；1 個 Pydantic serializer warning。 |
| Frontend lint/type/test | 通過 | ESLint、`tsc --noEmit`；35 files / 294 tests passed。 |
| Hardware-agent lint | 通過 | `ruff check` 全綠。 |
| Hardware-agent format | 失敗 | 7 個檔案需格式化。 |
| Hardware-agent mypy | 通過 | 34 source files，0 issue。 |
| Hardware-agent pytest | 通過 | 148 passed；92.22% coverage；1 個 brother-ql deprecation warning。 |
| OpenAPI 合約漂移 | 通過 | 重新匯出 `openapi.json` 與 `api-types.ts` 後 `git diff --exit-code` 通過。 |
| 全路由 UI 走查 | 通過 | 13 routes；0 pageerror；0 console.error；0 jargon。 |
| 29 張截圖人工複核 | 部分通過 | 確認桌面主要頁可渲染；發現行動版全面溢出、CLERK 導覽未依權限收斂、身分證 QA 假通過與長資料證據不足。 |
| 完整門市 E2E | 部分通過 | 29/30；停在已不存在的 `.pur-tools > summary` selector。產品採購功能改用現行腳本重測 11/11 通過，因此是 E2E 腳本漂移。 |
| 盤點／活動／報表 smoke | 通過（截圖不完整） | 執行輸出記錄為 11/11、6/6、全部報表分頁與季粒度通過；目前 review 目錄未保留全部分頁與長區間截圖，不能據此證明資料量。 |
| Dependency audit | 失敗 | Frontend 1 個；backend 5 個／4 packages；hardware-agent 7 個／2 packages。 |
| Alembic | 部分通過 | 實跑的 `lucamp_e2e` 在 head；根 `.env` 指向的 `lucamp` 停在 `a9b0c1d2e3f4`，落後 11 migrations，`alembic check` 失敗。 |

### 覆蓋率門檻的落差

整體 93.05% 很好，但 [backend/pyproject.toml](../backend/pyproject.toml) 只強制 overall 80%。這沒有落實 `CLAUDE.md` 規定的 `services/`、`domain/` ≥ 90%。本次輸出中 acquisition service 87%、einvoice service 86%、inventory service 86%、menu service 83%、signing service 86%；因此「pytest 全綠」仍不等於專案自訂的服務層門檻全綠。

## 4. 高優先（P0：release blocker）

### P0-1 更新有已知漏洞的依賴

**證據**

- Backend lock：cryptography 48.0.0、pydantic-settings 2.14.1、python-multipart 0.0.30、Starlette 1.2.1。
- Hardware-agent lock：Pillow 12.2.0、Starlette 1.2.1。
- Frontend lock：Next.js 路徑帶入 PostCSS 8.4.31。
- `pip-audit`／`pnpm audit --prod` 都以非 0 結束。

**最低修補版本**

- cryptography 48.0.1（[GHSA-537c-gmf6-5ccf](https://github.com/advisories/GHSA-537c-gmf6-5ccf)，High）。
- pydantic-settings 2.14.2（[GHSA-4xgf-cpjx-pc3j](https://github.com/advisories/GHSA-4xgf-cpjx-pc3j)，Moderate；目前程式未使用該 advisory 的 `NestedSecretsSettingsSource`，直接可利用性較低，但仍應升級）。
- python-multipart 0.0.31（[CVE-2026-53540](https://osv.dev/vulnerability/CVE-2026-53540)）。
- Starlette 1.3.1（同時修補 [PYSEC-2026-248](https://osv.dev/vulnerability/PYSEC-2026-248) 與 [PYSEC-2026-249](https://osv.dev/vulnerability/PYSEC-2026-249)；後者為 High）。
- Pillow 12.3.0（`pip-audit` 列出的 5 個 Pillow advisory 共用修補版）。
- PostCSS 8.5.10（[GHSA-qx2v-qp2m-jg93](https://github.com/advisories/GHSA-qx2v-qp2m-jg93)，Moderate；目前是 bundler 路徑，實際風險低於處理不可信 CSS 的服務，但修補版已存在）。

**安排**：透過相容的 FastAPI／Next.js 版本解決 transitive dependency，不直接硬壓不相容版本；更新 lockfiles 後重跑 backend、frontend、hardware-agent 全測試、OpenAPI drift、E2E 與三套 audit。

**效益**：移除已公開且已有修補版的攻擊面。
**範圍**：三個服務與 lockfiles。
**成本**：M（約 1–2 人日，取決於 FastAPI／Starlette 與 Next.js 相容性）。

### P0-2 保留永不自動登出的 UX，補齊可撤銷與裝置防護

**證據**

- [backend/app/core/config.py:39](../backend/app/core/config.py) 預設 `auth_session_never_expires = True`。
- [backend/app/core/security.py:47](../backend/app/core/security.py) 在此設定下省略 JWT `exp`，payload 也沒有 `jti` 或 session version。
- [frontend/lib/token.ts:10](../frontend/lib/token.ts) 與 `:37` 把 bearer token 存進 localStorage；目前登出只會清除該瀏覽器的 token，已被複製的 token 仍可使用。
- [frontend/next.config.ts:3](../frontend/next.config.ts) 沒有 CSP 等安全 headers；實際回應還有 `X-Powered-By: Next.js`。

**產品決策（2026-07-14 再確認）**：門市人員操作中不得因時間到期而被自動登出。後續改善不得引入會中斷 POS、收購或關帳流程的可見式 timeout。

後端 [backend/app/core/deps.py](../backend/app/core/deps.py) 已逐請求查 DB 覆核 `is_active`、role、store，且 [backend/tests/integration/test_deps_revalidation.py](../backend/tests/integration/test_deps_revalidation.py) 覆蓋停用、刪除與降權立即生效，這部分應保留。剩餘風險是單一 token 無法按裝置撤銷；若瀏覽器發生 XSS 或同機惡意程式讀到 localStorage，token 在帳號未停用或全站更換簽章金鑰前可持續重用。

**安排**：維持使用者感知上的永不過期 session；新增伺服器端 device session 或 `jti/session_version`，提供「撤銷本裝置／撤銷全部裝置」與密碼變更後撤銷；憑證可在背景無感輪替，不把輪替失敗直接變成操作中登出。瀏覽器憑證優先改為持久化 HttpOnly、Secure、SameSite cookie，或由受控門市殼層安全保存；加入 CSP、`frame-ancestors`、`nosniff`、referrer policy 並關閉 `poweredByHeader`。另提供明確的手動鎖定／交班入口，鎖定不等於結束 session。

**效益**：不打斷門市工作，同時讓遺失裝置、離職帳號與外洩 token 能被精準撤銷。
**範圍**：auth、device session、frontend token storage、部署 headers、登入與交班 E2E。
**成本**：L（約 3–5 人日，需 migration、管理入口與完整 auth regression）。

### P0-3 對齊資料庫 migration 與啟動檢查

**重現**

```bash
cd backend
uv run alembic current
uv run alembic heads
uv run alembic check
```

根 `.env` 的 `lucamp` 目前在 `a9b0c1d2e3f4`，head 是 `c1d2e3f4a5b6`，中間相差 11 個 migration；`alembic check` 回 `Target database is not up to date`。本次 UI 實跑沒有受影響，是因為正在運行的 backend 明確覆寫成 `lucamp_e2e`，且該 DB 已在 head。

**安排**：先備份 `lucamp`，在可回滾窗口執行 `alembic upgrade head`，再跑 API／E2E；啟動腳本加上 migration head preflight，避免程式對錯版 schema 啟動。

**效益**：避免換回一般開發 DB 或部署時才遇到缺欄位／狀態不相容。
**範圍**：資料庫、啟動／部署流程。
**成本**：S（半日內；若資料量大需另估 migration window）。

### P0-4 讓 release gate 真正全綠且符合專案規範

**問題**

- [check.sh:5](../check.sh) 與 [Makefile:6](../Makefile) 預設 `python -m uv`，目前 Linux 環境沒有 `python` 命令。
- `check.sh` mode 為 `644`，但文件要求直接執行 `./check.sh`。
- Backend 有 43 個檔案、hardware-agent 有 7 個檔案未通過 `ruff format --check`。
- [check.sh:10](../check.sh) 與 [Makefile:11](../Makefile) 沒有執行 hardware-agent、dependency audit、Alembic head 或 frontend production build。
- [backend/pyproject.toml:51](../backend/pyproject.toml) 只守 overall 80%，未守 service ≥90%。

**安排**：先在獨立 `chore/quality-gate-v001` 分支做純機械 format；修正 Unix executable bit 與 UV 偵測；把 hardware-agent、migration check、production build、audit 與服務層 coverage 檢查納入可重複執行的 gate。更新後從乾淨 checkout 跑一次，保留輸出。

**效益**：讓「全綠」重新具有可信且可重現的定義。
**範圍**：品質腳本、格式、測試設定。
**成本**：M（1–2 人日，含 coverage 補測）。

### P0-5 保護本機 secrets 檔案權限

`.env` 與 `backend/.env` 都是 `644`。雖然兩者未被 Git 追蹤，但同機其他使用者仍可讀取。

**安排**：改為 `chmod 600 .env backend/.env`；部署文件加入 owner／mode 驗證；確認 `backend/.env` 是否仍需要，若已不用則在人工確認後移除，避免雙來源漂移。

**效益**：降低 DB 密碼、JWT／PII／HMAC 金鑰的本機暴露。
**範圍**：部署與本機設定。
**成本**：XS（數分鐘）。

### P0-6 修正 UI QA 假通過與證據綁定

**截圖證據**

- `13-ui-defects/10-contact-invalid-nid-filled.png` 的新增表單填入 `A123456788`。
- `13-ui-defects/11-contact-invalid-nid-result.png` 的紅字是上方搜尋表單的「請輸入姓名或電話」，新增表單本身沒有出現身分證錯誤；腳本實際按到第一個 `button[type="submit"]`，不是「建檔」。
- [frontend/scripts/fully-e2e-qa.mjs:157](../frontend/scripts/fully-e2e-qa.mjs) 宣稱檢查「120 天資料」，但 `checkBigTables` 只重新載入 `/reports`、`/inventory`、等待 900ms 並檢查 page error，沒有 seed、日期範圍或資料筆數 assertion。
- `ui-sweep-summary.json` 因此同時把上述兩項記為通過，不能作為 release evidence。

**安排**：所有關鍵流程 selector 改用唯一 accessible name 或表單 scope；身分證案例需 assert 建檔 API 未送出、錯誤訊息位於新增表單且資料未新增；長資料案例先記錄 seed run id，再透過 API/DB assert 日期跨度與筆數，最後切到趨勢／庫存分頁並保存畫面。摘要 JSON 必須包含資料集識別、筆數、日期範圍與每張截圖對應 assertion。

**效益**：避免「畫面沒壞」被誤報成業務規則與長資料驗證通過。
**範圍**：UI QA 腳本、seed lifecycle、artifact metadata、release gate。
**成本**：M（約 1–2 人日）。

## 5. 中優先（P1）

### P1-1 修正窄螢幕導覽與頁面水平溢出

390px viewport 實測 `documentElement.scrollWidth = 816`；追加檢視的首頁、POS、報表、收購、庫存、採購 6 張窄螢幕截圖也全部是 816px 寬。主因是 [frontend/app/globals.css:180](../frontend/app/globals.css) 的 14 項水平 flex 導覽沒有 wrap、scroll container 或收合版；桌面 1366px 也已大量換成單字直排／多行。

截圖還顯示這不只是多一條水平 scrollbar：導覽文字逐字直排、報表 tab 從「庫存價值」後被截斷、庫存表格只看得到前三欄且右側內容無提示、主要內容只佔左側約 341px，右側留下大片不可用空間。

**安排**：桌面改為分組導覽或側欄；≤900px 使用 hamburger／drawer 或可辨識的橫向 tab scroller；保留目前 44px 高度基線並顯示 active route。以 390、768、1024、1366 四個 viewport 加 Playwright screenshot regression。

**效益**：手機／平板可用，桌面導覽更易掃描。
**範圍**：authed layout、global CSS、各頁 visual regression。
**成本**：M（1–2 人日）。

### P1-2 修正 E2E 腳本漂移並建立單一可信入口

[frontend/scripts/full-e2e-smoke.mjs:411](../frontend/scripts/full-e2e-smoke.mjs) 仍尋找已移除的 `.pur-tools > summary`，所以完整劇本在已成功新增供應商後 timeout。現行 [frontend/scripts/purchasing-smoke.mjs](../frontend/scripts/purchasing-smoke.mjs) 11/11 通過，證實產品功能正常。

另有 `fully-e2e-qa.mjs`、`manual_extra.mjs`、`manual_shots.mjs` 三支未追蹤腳本；其中全路由 QA 很有價值，但目前不屬於可重現 baseline。

**安排**：修正完整劇本 selector；將需要保留的 QA 能力整併進受版控腳本；建立一個 seed → services → smoke → artifact → cleanup 的入口，明確使用隔離 DB，避免腳本互相留下狀態。

**效益**：完整 E2E 的紅燈能代表產品問題，不再因 selector 漂移產生假警報。
**範圍**：frontend scripts、E2E 文件、測試資料 lifecycle。
**成本**：M（1 人日）。

### P1-3 Web Interface Guidelines 精確發現

以下依 2026-07-13 即時取得的 Vercel Web Interface Guidelines 檢查；動態掃描同時確認所有目前可見表單控制都有 accessible name、所有按鈕／連結都有名稱、主要頁面都有 1 個 H1。

## frontend/app/(authed)/layout.tsx
frontend/app/(authed)/layout.tsx:71 - missing skip link for main content
frontend/app/(authed)/layout.tsx:71 - nav has no small-screen containment/collapse; 390px viewport overflows to 816px
frontend/app/(authed)/layout.tsx:104 - main lacks stable id target for skip link

## frontend/app/globals.css
frontend/app/globals.css:125 - `:focus` → `:focus-visible`
frontend/app/globals.css:502 - modal backdrop missing `overscroll-behavior: contain`
frontend/app/globals.css:901 - `:focus` → `:focus-visible`
frontend/app/globals.css:1358 - `:focus` → `:focus-visible`
frontend/app/globals.css:1375 - `:focus` → `:focus-visible`
frontend/app/globals.css:1887 - animation missing `prefers-reduced-motion` variant

## frontend/app/login/page.tsx
frontend/app/login/page.tsx:38 - `autoFocus` also activates on mobile; gate to desktop or remove

## frontend/app/(authed)/reports/page.tsx
frontend/app/(authed)/reports/page.tsx:1659 - selected report tab is local state; sync to URL for deep links/back navigation

## frontend/app/(authed)/inventory/page.tsx
frontend/app/(authed)/inventory/page.tsx:1218 - selected inventory tab is local state; sync to URL

## frontend/app/(authed)/purchasing/page.tsx
frontend/app/(authed)/purchasing/page.tsx:1233 - selected purchasing tab is local state; sync to URL

## frontend/app/(authed)/contacts/page.tsx
frontend/app/(authed)/contacts/page.tsx:381 - selected contact tab is local state; sync to URL

**成本**：M（1–2 人日，可與 responsive nav 同一批處理）。

### P1-4 補齊可部署／可還原的完整服務入口

[docker-compose.yml:1](../docker-compose.yml) 明確只啟動 PostgreSQL，但 `CLAUDE.md` 的常用指令仍寫 `docker compose up -d` 可啟動 postgres + backend + frontend + hardware-agent；這會讓新環境誤以為系統已完整啟動。HTTP 回應目前也屬 dev server，沒有 production build 驗證。

**安排**：短期先修文件，提供四服務的明確本機啟動命令與 health aggregate；正式部署階段再補 production Compose／systemd、backup job、restore drill、restart policy 與 log rotation。品質 gate 加 `pnpm build`。

**效益**：降低環境差異與「DB 起了就以為系統起了」的誤判。
**範圍**：部署、文件、健康檢查。
**成本**：文件 S；完整部署 L。

### P1-5 依實際權限收斂導覽

`clerk-reports.png` 顯示 CLERK 頂部仍看到「報表」「設定」等管理入口；點進報表後只有「需管理者權限」與大片空白。後端 403 是正確安全邊界，但導覽仍把店員帶到無法完成工作的死路。

**安排**：由後端現值或可重新驗證的 capabilities 產生導覽；無權限項目預設隱藏。若業務需要讓店員知道功能存在，使用 disabled item 並在點擊前說明需要管理者，不要先進頁才顯示空白。升／降權後應立即重新取得 capabilities，不能只信永不過期 token 內的舊 role claim。

**效益**：減少誤觸與權限困惑，同時維持後端授權為唯一安全邊界。
**範圍**：authed layout、capability API/cache、role-change E2E。
**成本**：S–M（0.5–1 日）。

### P1-6 關帳前顯示應有現金與異動摘要

`10-cash-reconcile/01-cash.png` 及雙瀏覽器現金畫面只顯示開帳零用金、手動調整與實點金額；應有現金、當班收現、收購出帳、寄售付款與手動調整要到關帳成功後才看得到。這讓店員在輸入實點與送出不可逆關帳前缺少核對基準。

**安排**：在開帳中頁面加入唯讀的「目前應有現金」與異動分類摘要，提供查看 movement 明細；實點輸入後即時計算預估差異，非零時要求確認與備註。`送出調整`、`結帳` 不應使用完全相同的普通 primary 視覺，關帳需有明確確認步驟與 session 編號。

**效益**：降低錯帳、誤關帳與事後追查成本。
**範圍**：cash session read model/API、cash UI、audit 與 E2E。
**成本**：M（1–2 人日）。

### P1-7 改善大量寄售待付款作業

`03-consignment/01-consignment.png` 一頁有 20 筆、跨約四個月的待付款項，每筆只能個別按「付款」；目前只有寄售人手機搜尋，沒有依帳齡／金額排序、勾選同一寄售人合併付款或逾期提示。「本頁待付款合計」也可能被誤認為全部待付款總額。

**安排**：先顯示「全部待付款」與「目前篩選／本頁」兩種合計；加入售出日期、應付金額排序與帳齡標記；以寄售人分組並支援同一寄售人的多筆合併結算，付款確認列出筆數、總額與現金影響。保留單筆付款作為例外流程。

**效益**：縮短月結作業並降低漏付、重付與拿錯現金的風險。
**範圍**：consignment query/API、付款交易與併發控制、UI、收據／稽核。
**成本**：L（2–4 人日）。

### P1-8 統一日期格式與高風險操作層級

`08-campaigns/01-campaigns.png` 的輸入 placeholder 是 `mm/dd/yyyy`，列表卻顯示 `2026/6/29 上午7:38:56`；報表日期同樣使用美式輸入顯示。門市系統應固定 Asia/Taipei 與一致的 `YYYY/MM/DD HH:mm`，避免活動起訖或跨日報表誤判。

同一畫面中的「結束」「作廢」，以及收購作廢、現金調整與關帳，都使用接近一般次要或主要操作的樣式。應讓不可逆操作具有一致的 danger 樣式、影響摘要與確認文案；不要只靠按鈕文字區分。

**效益**：降低日期誤讀與不可逆操作誤觸。
**範圍**：日期元件／formatter、campaign、acquisition、cash、confirm dialogs。
**成本**：M（約 1 日）。

## 6. 低優先（P2）

### P2-1 拆分超大型 client component 與 service

- Frontend：reports 1,720 行、POS 1,423 行、purchasing 1,282 行、inventory 1,242 行、acquisition 1,193 行；global CSS 2,153 行。
- Backend：sales service 1,369 行、einvoice service 1,085 行、inventory service 1,016 行。

目前型別與測試都好，沒有證據顯示必須立即重寫。建議只在下次修改相同區域時，沿功能邊界抽出純 helper、表格／dialog component 與單一責任 service；不要做一次性大重構。

**效益**：降低 review 成本與修改衝突。
**範圍**：逐模組。
**成本**：L，分批隨功能處理。

### P2-2 清理 warning 與文件漂移

- Backend pytest 有 1 個 `PydanticSerializationUnexpectedValue` warning（測試把字串 `CASH` 傳給 enum 欄位）。
- Hardware-agent 有 brother-ql deprecated `logger.warn` warning，來自第三方套件。
- [backend/app/main.py:3](../backend/app/main.py) 還寫「目前僅提供 `/health`」，與大量已掛載 router 不符。
- [docs/current-status.md:12](current-status.md) 指向不存在的 `feat/acquisition-ui` worktree；實際 worktree 是 `feat/stocktake`。
- [docs/current-status.md:28](current-status.md) 把 purchasing／stocktake 列為 future/partial，但 UI、API 與 smoke 已存在並通過。

**安排**：把 warning 納入可見但不立即阻擋的追蹤；更新 current-status 與啟動說明，讓規劃文件再次可用。

**效益**：減少真正 warning 被噪音掩蓋，以及依錯誤狀態規劃工作。
**成本**：S。

### P2-3 性能先量測，不先猜測重構

本次截圖能證明桌面庫存與寄售列表各渲染約 20 列時沒有明顯版面錯亂；不能證明 120 天報表或大量資料效能。API list 大多有限制 50–200，現階段仍不建議僅因 `.map()` 或檔案很大就直接加入 virtualization。

**安排**：先在 production build 加入 route bundle size、API p95、報表查詢時間與 200-row render 的 budget；超標後才針對 pagination、index、query cache 或 virtualization 優化。

**效益**：把效能工作放在真瓶頸。
**成本**：S–M。

### P2-4 改善正常空狀態與長表單節奏

- `04-pos-sales/01-pos-empty.png` 在正常空購物車時，付款卡以紅色錯誤顯示「購物車是空的」；此時尚未發生錯誤，應改為中性的操作提示，只有使用者嘗試結帳時才顯示 validation error。
- `05-menu-fnb/01-menu-manage.png` 與 `06-purchasing/01-purchasing.png` 的空狀態只有「尚無…／尚無符合…」，可直接提供建立品項／切換篩選的下一步。
- 收購、會員與設定在 1366px 仍固定約 520px 寬並留下大片空白；行動版則變成很長的單欄。可在桌面使用主表單＋sticky 摘要／送出區，在手機保留單欄但提供目前步驟、錯誤總覽與固定底部主要操作。

**效益**：減少把正常狀態誤認成錯誤，並降低長流程漏填或捲動後找不到送出按鈕的成本。
**範圍**：POS、menu、purchasing、acquisition、contacts、settings 的文案與 layout。
**成本**：S–M（0.5–1.5 日，可隨 responsive 改造處理）。

## 7. Quick wins（建議依序）

1. `chmod 600 .env backend/.env`，並確認 `backend/.env` 是否仍需要。
2. 備份後把根 `.env` 的 `lucamp` 執行 `alembic upgrade head`；立即驗證 health、登入與 E2E。
3. 建 `chore/quality-gate-v001` 分支，純機械格式化 50 個檔案；不要混入行為變更。
4. 把 `check.sh` 設 executable，UV 預設改為可攜偵測；確認 Linux／Windows 入口一致。
5. 修正 `full-e2e-smoke.mjs` 的 stale purchasing selector。
6. 修正 `fully-e2e-qa.mjs` 的身分證 selector 與虛假的 120 天 assertion，讓 summary 附上資料筆數／日期跨度。
7. 依角色隱藏無權限導覽，避免 CLERK 進入只有「需管理者權限」的空白報表頁。
8. 升級 cryptography、Starlette、python-multipart、Pillow、pydantic-settings、PostCSS，重跑 audit 與全套測試。
9. 更新 `docs/current-status.md` 與 backend app docstring，移除過時 worktree／future 狀態。

## 8. 建議執行順序與完成定義

### Wave A：基線可重現（0.5–1 日）

- 修 secrets 權限、migration drift、check 入口、format、stale E2E selector。
- 完成定義：乾淨 checkout 上一個命令可跑完三服務 lint／format／type／test、合約與 E2E；全部 exit 0。

### Wave B：依賴與不打斷操作的 auth hardening（2–5 日）

- 升級 vulnerable dependencies、加入 audit gate、安全 headers。
- 維持 session 不因時間自動登出，新增可撤銷 device session、背景無感輪替、手動鎖定與安全憑證儲存。
- 完成定義：audit 0 known vulnerability；停權、降權、撤銷單機／全部裝置、登出、重開瀏覽器、交班鎖定與 XSS 防禦情境都有測試，正常操作不會因時間到期被中斷。

### Wave C：responsive 與 accessibility（1–2 日）

- 收合導覽、skip link、focus-visible、reduced motion、modal overscroll、URL-synced tabs。
- 完成定義：390／768／1024／1366 無非預期水平溢出；鍵盤可走完登入與 POS；Playwright screenshots 通過。

### Wave D：部署與長期維護（按 Phase 7）

- Production build、完整服務管理、backup／restore drill、觀測與效能 budget。
- 完成定義：新機依文件可重建服務；備份可在隔離環境還原；restart 後 health 與 smoke 自動通過。

## 9. 已做得好、不要任意改動的部分

- 金額與現金流程有大量 transaction／concurrency／rollback 測試；整體 coverage 93.05%。
- API 合約由 OpenAPI 生成 TypeScript，這次實測無 drift。
- Auth 會逐請求回 DB 覆核 active、role 與 store；KIOSK 與店務依賴分離。
- PII 採 AES-GCM、national ID blind index、敏感讀取與重要操作稽核的設計方向正確。
- PostgreSQL port 只綁 `127.0.0.1`；`.env` 未入 Git；必要金鑰沒有程式內預設。
- 跨店 `store_id` 與 DB 層不變量、冪等鍵、現金班別等核心約束已有明確測試。
- UI 的繁中用詞、表單 accessible name 與按鈕名稱整體良好；全路由沒有 JS error 或裸 enum jargon。空狀態與錯誤提示仍依 P0-6、P2-4 修正，不把這次 QA summary 當作 validation 已通過的證據。
- 桌面庫存與寄售付款表格在 20 列資料下仍容易掃讀，金額欄對齊、持有／狀態 badge 的方向正確；改善 responsive 時不應把桌面資料密度整套推翻。
- 首頁以門市任務卡呈現主要功能，對新進店員比抽象儀表板更直接；保留任務導向，只需改善導覽分組與角色可見性。
- Hardware-agent 以 DI 切換 Fake／real，阻塞硬體 I/O 有移出 event loop；不應為了簡化而把真機 I/O 塞回 backend。
- 報表與庫存在目前可確認的 20 列／單日畫面下沒有明顯渲染錯亂；沒有長區間量測證據前，不應宣稱大資料效能已通過，也不應先做高風險大重構。

## 10. 本次未執行的變更

- 沒有修改產品程式碼、依賴、migration、資料庫 schema、`.env` 權限或既有未追蹤 QA 腳本。
- 沒有推送 tag、commit 或遠端分支。
- 有在隔離的 `lucamp_e2e` 產生測試會員、收購、銷售、供應商、盤點與活動資料；沒有清理原本就存在的開帳班別，避免破壞既有測試狀態。
- 唯一 repo 內容變更是本評估文件；另在目前 `main` commit 建立本機 `v0.0.1` tag。
