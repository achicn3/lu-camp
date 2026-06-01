# Phase 0 — 狀態（chore/phase0-scaffold）

> **未合併 main。** 後端關卡全綠；前端關卡因本機環境 0xC0000005 無法在此驗證。
> 待前端關卡在本機（或排除 AV 干擾後）跑綠，再合併。

## ✅ 已完成且本機驗證綠燈

### 後端（backend/，uv）
- 依 docs/05 佈局：`app/main.py`（`/api/v1/health`，含 `response_model`/`operation_id`/`tags`）、`core/ modules/ shared/ scripts/`、`tests/`。
- 相依已實際安裝（FastAPI / SQLAlchemy 2 / Pydantic v2 / Alembic / asyncpg / argon2-cffi / pyjwt；dev：pytest/pytest-asyncio/pytest-cov/httpx/ruff/mypy），鎖於 `uv.lock`。
- **四道門全綠**：`ruff check` ✓、`ruff format --check` ✓、`mypy --strict` ✓（9 files）、`pytest` ✓（1 passed，coverage 100% ≥ 80）。
- `app/scripts/export_openapi.py` 產生確定性 UTF-8/LF `frontend/openapi.json`。

### 硬體代理（hardware-agent/，uv）
- `agent/escpos_printer.py`（`FakePrinter` + Code 128 標籤 + 錢櫃 kick）、`agent/main.py`（localhost `/health`、`/print/label`、`/drawer/open`）、`tests/`。
- **全綠**：`ruff` ✓、`ruff format` ✓、`mypy --strict` ✓（5 files）、`pytest` ✓（4 passed，coverage 98%）。

### 品質關卡彙整
- `Makefile`（`check` = backend-check + contract-check + frontend-check）、`check.ps1`、`check.sh`。
- **`mingw32-make backend-check` 全綠**（exit 0）。
- 防呆驗證：植入壞 import（`import totally_made_up_module` + 未定義 `ghost_function`）→ `ruff`（F401/F821）、`mypy`（import-not-found/name-defined）、`mingw32-make backend-check` 皆**紅燈**（已貼輸出），移除後恢復綠燈。

### 版本控制 / 工具
- 單一 `lu-camp/.git`（已移除外層損壞的 stray `.git`）；`core.fileMode=false`。
- remote `origin` → github.com/achicn3/lu-camp（僅備份/同步、不跑 CI）。
- `.gitignore` 含 `.claude/worktrees/`；`.gitattributes` 釘生成物為 LF。
- Claude Code 2.1.159（≥ 2.1.50，支援 worktree）；`.claude/agents/` 有 `code-reviewer`、`tdd-implementer`。

## ⛔ 待辦：前端關卡無法在此環境驗證
本機 Node 工具鏈會**間歇性 0xC0000005（access violation）/ Segmentation fault**：
- `openapi-typescript`（v7 與 v6）**每次**崩潰 → `frontend/lib/api-types.ts` **尚未生成/未入庫**。
- `tsc`、`vitest`、`eslint` 間歇崩潰（同一指令時綠時崩）。
- 連帶：`contract-check`（合約漂移）與 `frontend-check`（eslint/tsc/test）**無法在此驗證**。

研判為環境問題（Windows Defender/AV 即時掃描殺掉新生 `node` 子行程，或代理子行程派生不穩），非程式問題；同類崩潰亦出現在最初 `create-next-app` 的 `unrs-resolver`/`sharp` postinstall。

### 解除後要做（合併前）
1. 排除 AV 干擾（為 repo 與 `node.exe`/`pnpm` 加 Defender 例外或關 Controlled Folder Access），或於本機正常終端執行。
2. 跑前端段並補綠：
   ```
   cd frontend && pnpm install      # 若 node_modules 未就緒
   pnpm gen:api                     # 生成 lib/api-types.ts 並入庫
   pnpm lint && pnpm typecheck && pnpm test
   ```
3. 全部 `mingw32-make check`（或 `./check.ps1`）綠 → `code-reviewer` APPROVE → 合併回 main、push。

## 前端現況（檔案/管線已就緒，未驗證）
- Next.js 16 / React 19（TS strict、App Router、ESLint）；`openapi-fetch` + `openapi-typescript` + `vitest`。
- `package.json` scripts：`lint`/`typecheck`/`test`/`gen:api`。
- `lib/api.ts`（型別化 client，import 生成的 `./api-types`）、`lib/money.ts` + `__tests__/money.test.ts`、`vitest.config.ts`、`frontend/openapi.json`。
- ⚠️ `lib/api-types.ts` 待 `pnpm gen:api` 生成；在此之前 `tsc` 會因缺檔報錯（屬預期，合約管線要求先 gen）。

## 未在此驗證的其他項
- `docker compose build`（Dockerfile 為最小骨架，未建置驗證）。
