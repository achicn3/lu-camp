# 12 — 版本控制與分支工作流（Git，強制）

目標：**永遠不在 `main` 上直接開發**；每個功能走獨立短命分支 → 本機審查與檢查 → 合併回 `main` → push remote。無相依的功能用 Claude Code 原生 worktree 並行。**本專案不使用 GitHub CI；品質關卡全部在本機執行。** 版本控制是安全網，必須嚴格遵守。

## 1. 採用模型：單 main + 短命 feature branch（本機審查）

只有一條長期分支 `main`，不另開 `develop`（單店、單/少人、逐功能持續交付，整合緩衝線無實益）。

- `main`：永遠保持可部署，**禁止直接 commit/push**（一律經 feature 分支合併）。
- 每個功能/任務從**最新的 `main`** 開短命分支（數小時～一天內合併），命名：
  - `feat/<scope>-<簡述>`（功能，如 `feat/acquisition-bulk-lot`）
  - `fix/...`、`refactor/...`、`test/...`、`chore/...`
- 一個分支只做一件事，小而可審查。

## 2. 沒有 CI 後，關卡在本機（強制紀律）

拿掉 GitHub CI 後沒有伺服器端客觀關卡，能否保持 `main` 乾淨**全靠本機紀律**。因此合併進 `main` 前，下列**本機關卡一律必須真的執行、且貼出綠燈輸出**（不接受「應該會過」）：

1. **本機四道門**：`ruff` + `mypy --strict`（前端 `eslint` + `tsc --strict`）→ `pytest` + 覆蓋率達標（前端測試）→ **API 合約漂移檢查**（後端改動須重生成 `openapi.json`/`api-types.ts`，與版控比對無差異，見 `docs/11`）。
2. **`code-reviewer` 子代理**審查 diff，對照 `docs/08` 檢查清單，回 `APPROVE`（問題須先修完）。金額/發票/現金/PII 從嚴。

**合併前提＝本機四道門全綠 且 code-reviewer 回 APPROVE**。任一不滿足不得合併。

> remote 角色：GitHub（或任一 remote）僅作備份/同步，不跑 CI。Claude Code 的 `@claude` GitHub App 對本流程非必要。

## 3. 每個功能的分支→合併流程（標準步驟）

```bash
# 1) 從最新 main 開分支
git switch main && git pull --ff-only origin main
git switch -c feat/<scope>-<簡述>

# 2) TDD：紅 → 綠 → 重構，小步 commit（建議 Conventional Commits）

# 3) 合併前先 rebase 到最新 main（至少一次，確保使用最新分支）
git fetch origin && git rebase origin/main
#   解完衝突、確認可運行

# 4) 本機關卡（必須真的跑、貼綠燈輸出）
uv run ruff check . && uv run mypy .
uv run pytest            # 含覆蓋率
pnpm lint && pnpm typecheck && pnpm test
#   + 合約漂移檢查（見 docs/11）
#   + code-reviewer 子代理審查 → APPROVE

# 5) 先 push feature 分支到 remote（備份）
git push -u origin feat/<scope>-<簡述>

# 6) 合併回 main（已 rebase 故為快轉；或用 --squash 收斂為單一 commit）
git switch main && git pull --ff-only origin main
git merge --ff-only feat/<scope>-<簡述>
#   （如要單一 commit：git merge --squash 後再 commit）

# 7) 合併後一定要 push main 到 remote
git push origin main

# 8) 清理分支
git branch -d feat/<scope>-<簡述>
git push origin --delete feat/<scope>-<簡述>
```

要點：
- **每次完成 feature 都要 push remote**：先 push feature 分支（步驟 5），合併後再 push `main`（步驟 7）；不讓任何進度只存在本機。
- **agent 從 main 開分支務必用最新的**：開分支前 `pull --ff-only`；合併前 `git fetch && git rebase origin/main` 至少一次。並行時尤其重要（見 §4）。
- commit 訊息小而清楚。

## 4. 並行：無相依功能用 worktree（Claude Code 原生）

Claude Code 子代理共用同一工作目錄，**不能**直接讓兩個子代理同時在兩條分支寫——除非用 **git worktree**（每條分支一個獨立工作目錄）。Claude Code v2.1.50+ 原生支援：

- **子代理並行**：在實作型子代理（`tdd-implementer`）的 frontmatter 加 `isolation: worktree`，主 session 即可同時派多個子代理，各自在隔離 worktree 的獨立分支上實作、commit，互不干擾。唯讀的 `code-reviewer` **不需要**隔離。
- **手動並行**：`claude --worktree feat/<branch>` 在另一終端開獨立 session。
- worktree 預設從最新 `main` 分出；把 `.claude/worktrees/` 加入 `.gitignore`。

**只並行「真正獨立、不碰共用檔」的功能**（依 `docs/07` 相依圖判斷）。以下一律**序列**，不可並行：
- 動到 **Alembic migration**（避免 revision 衝突）。
- 動到 `shared/enums.py`、`core/*` 等共用檔。
- 同一模組的多個變更。

並行合併紀律：每條分支合併**前**都要 `git fetch && git rebase origin/main`（因為 main 會隨其他分支合併而前進），通過本機關卡與 code-reviewer 後，**依相依順序逐一合併**（被依賴者先合併；後者 rebase 到含前者的最新 main 再合）。一次只合一條、合完即 push，避免互相覆蓋。

## 5. 與既有審查層的關係（見 `docs/08`）
- Layer 1 自動門 = **本機四道門 + 合約漂移**必過（客觀硬底線，於本機執行）。
- Layer 2 AI 審查 = `code-reviewer` 子代理審 diff 並回 `APPROVE`／`REQUEST_CHANGES`；**這是合併核准關**（取代人工核准）。
- Layer 3 人工 = **預設不需要**；你可選擇性抽查（尤其金額/發票/現金/PII）。

## 6. 對應到 Phase（見 `docs/07`）
- 一個 Phase = 一組 feature 分支；每個任務一條分支。
- Phase 內可並行的葉節點任務用 worktree 並行；跨 Phase 仍依相依順序。
- Phase 收尾：確認該 Phase 所有分支已（本機關卡綠 + code-reviewer APPROVE）合併進 `main`、已 push remote、可 `docker compose up`。

## 7. 注意事項
- 確認 Claude Code 版本支援 worktree（v2.1.50+）；不支援就退回「一次一條分支序列開發」。
- 不要在 worktree 間共用會衝突的狀態（同一個 DB/port）；測試用各自的測試 DB/容器。
- 絕不對 `main` force-push；以 rebase + 快轉（或 squash）保持乾淨線性歷史。
- 沒有 CI＝沒有伺服器端守門，**本機關卡的執行紀律就是底線**；務必真的跑、真的貼輸出。