# CLAUDE.md — 實作規範（最高優先）

本檔是 Claude Code 在本專案的最高優先指引。**任何實作都必須遵守以下規則；若規格文件與本檔衝突，以本檔為準；若需求不明確，先停下來詢問，不要自行臆測。**

## 0. 規格文件位置（動工前必讀）

- 全部規格文件位於專案根目錄的 **`docs/`** 資料夾：`docs/README.md`、`docs/01-requirements.md` … `docs/12-git-workflow.md`。
- **開始任何 Phase / 模組前，先讀過 `docs/README.md` 與該工作相關的 `docs/NN-*.md`。** 本檔中提到的 `NN-*.md` 一律指 `docs/NN-*.md`。
- 工作流程、避免幻想 code 的防呆機制、Code Review 程序，見 `docs/08-workflow.md`，亦為強制。

---

## 1. 開發方法：TDD（強制）

- **每一步都要實際執行驗證，不可假設程式會動**：寫完即在本機跑 `pytest` / `ruff` / `mypy`（前端 `tsc` / 測試），全綠才算完成。測試必須真的執行通過，不是「看起來會過」。
- **禁止幻想**：使用任何套件/模組/函式前，先確認它已安裝且 API 確實存在（看官方文件或實際 import 跑一次）；不確定就查、不要臆測簽名。失敗的測試、`ruff`（未定義名稱/錯誤 import）、`mypy`（型別/不存在的屬性）就是擋幻想的第一道牆。

- **Test-first**：先寫會失敗的測試，再寫讓它通過的最小實作，再重構。不允許「先實作後補測試」。
- 每個 feature/bugfix 的 PR 必須包含對應測試。
- 領域邏輯（services）與金額計算必須有單元測試；API 端點必須有整合測試；關鍵流程（收購、結帳、寄售結算、現金對帳）必須有端對端測試。
- 覆蓋率門檻：`services/`、`domain/` ≥ 90%；整體 ≥ 80%。本機關卡未達標即失敗（本專案不使用 GitHub CI）。
- 詳見 `06-tdd-strategy.md`。
- **前端 UI 變更必跑瀏覽器 E2E（強制）**：任何動到前端可見畫面/流程的 task，完成定義都包含「依
  `docs/20-browser-e2e-smoke.md` 對真 backend + 真 Postgres 跑一次 Playwright 煙霧並截圖」，且每個
  有 UI 的功能要有對應 `frontend/scripts/<feature>-smoke.mjs`、回報附上截圖（見 `docs/08` §6.1）。
  純後端 task 不需要；環境跑不起來須誠實回報卡點、不得宣稱「已 e2e」。

## 2. 專案結構（強制，不得擅自更動）

- **採單一 monorepo**：根目錄即本 repo 根 `lu-camp/`，底下 `backend/`、`frontend/`、`hardware-agent/` 為並列的最上層資料夾，`CLAUDE.md`、`docker-compose.yml`、`docs/` 已置於根目錄。**禁止把前端與後端拆成不同 repo**；前後端的變更（尤其 API 合約變動）應在同一 repo、可在同一 commit 一起改。所有程式碼都建立在 `lu-camp/` 樹之內，依 `05-project-structure.md` 的完整佈局放置（不另建 `store-system/` 子資料夾）。
- 後端採 **模組化單體（modular monolith）**，依 `05-project-structure.md` 的目錄與分層組織。
- 後端分層方向固定：`router → service → repository → model`。
  - `router`：只做 I/O 與驗證（Pydantic schema），不含業務邏輯。
  - `service`：所有業務邏輯與不變量，唯一可協調多 repository 的層。
  - `repository`：唯一可直接碰 DB/ORM 的層。
  - 跨模組呼叫只能透過對方的 `service` 介面，**禁止跨模組直接存取對方的 repository 或資料表**。
- 新增檔案/資料夾前先確認結構文件；若需偏離，先詢問。

## 3. 技術約束

- 後端：Python 3.12+、FastAPI、SQLAlchemy 2.0（typed, async）、Pydantic v2、Alembic 管理 migration。
- 前端：Next.js App Router + TypeScript（strict）。
- 套件管理：後端一律用 `uv`；前端用 `pnpm`。
- Lint/format/type：後端 `ruff` + `mypy`（strict）；前端 `eslint` + `prettier` + `tsc --noEmit`。本機檢查全綠才可合併。
- 所有 schema 變更都要有 Alembic migration，禁止手改 DB。
- **API 合約優先**（見 `docs/11-api-contract.md`）：後端 OpenAPI 是 API 的唯一事實來源；後端端點須有完整 `response_model` 與 `operation_id`。前端一律使用「由 OpenAPI 生成的型別化 client」呼叫 API，**禁止手刻 API 型別、禁止為了理解 API 去反推後端原始碼**。改後端 API 就要更新生成物，本機合併前會比對生成物是否最新、不符即失敗。

## 4. 多分店就緒（強制）

- **每一張業務資料表都必須有 `store_id` 欄位**（外鍵到 `stores`），現階段只有一間店但值都要填。
- 嚴禁寫死「只有一間店 / 一個倉庫 / 一個收銀台」的假設。
- 查詢預設以 `store_id` 範圍過濾；「總部/管理者」角色可跨店彙整查詢。

## 5. PII / 安全（強制）

- `身分證字號 (national_id)` 與其他敏感個資：**靜態加密儲存**（欄位層級加密），金鑰由環境變數/KMS 管理，禁止寫入程式碼或 repo。
- **national_id 不可明文/部分搜尋**：另存 `national_id_blind_index = HMAC(national_id, 金鑰)` 獨立索引欄，僅供**精確去重比對**（避免同一賣方重複建檔）；日常找人以姓名/電話查詢。HMAC 金鑰同樣由環境/KMS 管理、不入 repo。
- 含 PII 的欄位：禁止寫入 log、禁止出現在一般 API 回應；僅授權角色可解密查看，且每次查看都要寫 `audit_log`。
- 所有敏感操作（作廢發票、改價、現金調整、PII 查看、權限變更）一律寫 `audit_log`（誰、何時、對象、前後值）。
- 密碼以 `argon2`/`bcrypt` 雜湊；認證用 JWT（短效）+ refresh。

## 6. 金額與發票（強制）

- 一律用 `Decimal`（後端）/ 字串（前端傳輸）處理金額，**禁止用 float**。
- **幣別新台幣、金額一律整數元（無角分）**：內部用 `Decimal` 計算、邊界以 **ROUND_HALF_UP quantize 到整數元**（`core/money.py` 的 `round_ntd()`）。
- **標價含稅**：`unit_price`/`listed_price` 皆為含稅價。稅於**發票總額層級**推算一次（不逐項算稅）：`net = round_ntd(total / (1 + tax_rate))`、`tax = total − net`（保證 `net + tax = total`，不差一元）。`tax_rate` 放 `settings`、預設 5%。
- `core/money.py` 提供並測試：`round_ntd()`、`split_tax_inclusive(total, rate) -> (net, tax)`、`commission(gross, pct) -> amount`、定價計算（見 §7）。
- **發票開關 `einvoice_enabled`**：不論開或關，**每一筆銷售都必須完整寫入 `sales`**。開關只決定是否產生電子發票並送 Turnkey。關閉時，銷售標記 `invoice_status = NOT_ISSUED`，可日後補開/補對帳。
- 稅率、抽成預設值（寄售 50%）、發票開關等都放 `settings`，可設定，不得寫死於程式邏輯。
- 電子發票 MIG XML 與 Turnkey 交換的實作，須先確認當前 MIG/Turnkey 版本（目前為 MIG 4.0/4.1、Turnkey v3.2），不得依憑記憶硬寫欄位。詳見 `01-requirements.md` 的「電子發票」段落。

## 7. 領域核心不變量（必須以測試守護）

1. 序號商品（serialized item）一旦 `SOLD` 不可再被售出或重複入庫。
2. 寄售商品賣出時（`commission_pct` 為整數百分數，預設 50）：`抽成金額 = round_ntd(售價 × commission_pct / 100)`、`應付寄售人 = 售價 − 抽成金額`，並產生 `consignment_settlement`（狀態 `PENDING`）。
3. 買斷商品毛利 = 售價 − 收購成本；寄售商品店家收入只認抽成，不認全額售價。
4. 現金抽屜對帳：`結帳應有現金 = 開帳零用金 + 銷售現金收入 − 收購付出 − 寄售付款 ± 手動調整`；差異需記錄。
5. 退貨且原銷售已開發票時，必須產生折讓單（allowance）而非直接刪除發票。
6. 散裝批（bulk_lot，E 級）：售出按該堆 `unit_price` 計價、`remaining_qty` 扣減後不得 < 0、歸零轉 `SOLD_OUT`；每件成本 = `acquisition_cost ÷ total_qty`。各堆價格相互獨立。
7. 退已售寄售品須反轉 `consignment_settlement`（未付→`CANCELLED`；已付→`reclaim_needed=true`），不可留下虛假應付或已實現抽成。
8. 影響現金的操作（收現、收購/散裝付現、寄售付款、退貨退現）必須在開帳中的 `cash_session` 下進行，否則拒絕並提示開帳。
9. 收購定價輔助（定價計算機）：`建議售價 = round_ntd(收購價 ÷ (1 − margin_pct/100))`，為含稅整數元；`margin_pct` 為整數百分數（`default_margin_pct` 放 `settings`，預設 45），店員可手動覆蓋。**邊界：`margin_pct` 限 0–99**（≥100 會除以零/負值），超出範圍必須被擋下並回錯。

## 8. 溝通規則

- 規格不清楚、或某決策會影響資料模型/模組邊界時，**先問再做**。
- 不要為了通過測試而弱化測試；不要繞過上述任何約束。
- 重大設計選擇以 ADR 形式記錄於 `docs/adr/`（沿用 `02-architecture.md` 的格式）。

## 9. 程式碼品質慣例（商業級）

- **所有 import 一律放在檔案最上方**（PEP8；後端由 `ruff` E402 強制，前端 import 置頂並排序）。除非為打破循環相依，否則禁止函式內 import。
- **完整型別註記**：後端 `mypy --strict`、前端 `tsc --strict` 全綠；公開的 service 方法要有 docstring/型別。
- 函式短小、單一職責；命名有意義；不留死碼、不留被註解掉的程式碼。
- 錯誤處理明確：用 `shared/exceptions.py` 的自訂例外，禁止裸 `except:`／吞例外。
- 不寫魔術數字/字串：常數放設定或 `shared/enums.py`。
- 日誌結構化且**不含 PII**。
- 邊界輸入一律以 Pydantic 驗證；金額用 `core/money.py` 的 Decimal 工具。
- 每個檔案聚焦單一關注點，符合 `05-project-structure.md` 的分層。

## 10. 版本控制（Git，強制；見 `docs/12-git-workflow.md`）

- **禁止在 `main` 直接 commit/push**。只有一條長期分支 `main`（不用 develop）；每個功能從**最新的 `main`** 開短命分支（`feat/…`、`fix/…`、`refactor/…`、`test/…`、`chore/…`），一分支只做一件事。
- **本專案不使用 GitHub CI；品質關卡全在本機執行。** 合併進 `main` 前必須：本機四道門全綠（`ruff`/`mypy`、`pytest`+覆蓋率、前端 `eslint`/`tsc`/測試、API 合約漂移檢查，須真的執行並貼出綠燈輸出，不接受「應該會過」）**且**通過 Codex 審查（見下「Code Review 採 Codex」）。
- **合併前先 rebase**：`git fetch && git rebase origin/main`（至少一次，確保用最新 main）。
- **每次完成 feature 都要 push remote**：先 push feature 分支，合併進 `main` 後再 push `main`；不讓進度只留本機。合併用快轉或 `--squash`，完後刪分支。
- **並行**：無相依、不碰共用檔的功能才並行；實作型子代理用 `isolation: worktree` 各自隔離分支；合併前各自 rebase 到最新 main，依相依順序逐一合併並 push。動到 Alembic migration / `shared/enums.py` / `core/*` / 同一模組者一律**序列**。
- 不對 `main` force-push；以 rebase + 快轉（或 squash）保持乾淨線性歷史。

### Code Review 採 Codex（codex-plugin-cc，強制）

- **主要審查者改為 Codex**（OpenAI `codex-plugin-cc`，在 Claude Code 內執行），取代原本的 `code-reviewer` 子代理。
- **每個 task 合併前的審查流程**：
  - 一般 task：合併前跑 `/codex:review --base main`。
  - 高風險 task（涉及金額／現金／發票／併發／PII／rollback，例如 T11）：跑 `/codex:adversarial-review --base main`，重點挑 race condition、失敗回滾、資料一致性。
  - Codex 回報問題 → 修正 → 再跑一次 Codex review → 重複直到 Codex 無重大意見。
  - **每一輪修正都要停下讓使用者確認，不得無人看管自動循環。**
- **不啟用 review gate（Stop hook 全自動）**：官方警告其會造成 Claude/Codex 長迴圈、快速燒用量；除非使用者明確要求並在場盯著，否則一律保持關閉。
- **Codex 意見的定位**：是「建議」，**不得凌駕本機四道門與測試這個客觀底線**。
- **採納 Codex 意見修正後**：必須跑過四道門全綠 **＋ 全部既有測試仍綠**，證明沒有把功能改壞，才算修完。
- **嚴禁為了消除 Codex 意見而**：刪除／弱化既有測試、修改測試使其通過、捏造輸出、或做與該 task 無關的擅自更動。修正必須針對 Codex 指出的真實問題，且有測試與四道門背書。

## 常用指令（實作後補齊實際指令）

```bash
# 後端
uv run pytest                 # 測試
uv run ruff check . && uv run mypy .
uv run alembic upgrade head   # 套用 migration

# 前端
pnpm test
pnpm lint && pnpm typecheck

# Git（單 main、本機審查、合併後 push）
git switch main && git pull --ff-only origin main
git switch -c feat/<scope>-<簡述>
git fetch origin && git rebase origin/main     # 合併前先 rebase 到最新 main
git push -u origin feat/<scope>-<簡述>          # 先推 feature 分支
git switch main && git merge --ff-only feat/<scope>-<簡述>
git push origin main                            # 合併後務必 push main

# 全系統
docker compose up -d          # 啟動 postgres + backend + frontend + hardware-agent
```