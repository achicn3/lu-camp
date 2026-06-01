# 09 — Claude Code 開工 Playbook

把這份當成你「怎麼一步步驅動 Claude Code」的腳本。流程分三階段：**理解 → 規劃 → 執行（含並行與審查）**。每段都有可直接貼的提示詞。

> 前置：`docs/` 已就緒、`CLAUDE.md` 已放專案根目錄。先用 `/plan` 進規劃模式，避免它一開始就亂寫。

---

## 階段 1：理解需求（不寫任何 code）

進規劃模式後貼：

```
請先完整閱讀 docs/ 底下所有文件，特別是 CLAUDE.md、docs/01-requirements.md、
docs/02-architecture.md、docs/03-data-model.md、docs/05-project-structure.md、
docs/06-tdd-strategy.md、docs/07-roadmap.md、docs/08-workflow.md。

讀完後，先不要寫任何程式碼，請輸出：
1. 你對本系統的理解摘要（業務模型、四型態庫存含 E 級散裝批、雙向現金、發票開關、PII 要求）。
2. 你認為文件中仍模糊或互相矛盾的地方（列成問題清單）。
3. 對照 07-roadmap.md，把全部工作拆成可執行的任務清單，標出每個任務的相依關係與
   優先級，並標示哪些任務彼此「真正獨立、不碰共用檔（migration / shared enums /
   同一模組）」因此可並行。

完成後停下來等我確認，不要開始實作。
```

→ 你檢視它的理解與問題清單，把模糊處答清楚（例如成色分級、稅務設定），確認任務拆解與相依判斷正確再往下。

---

## 階段 2：建立防呆地基與審查代理（Phase 0）

確認理解後貼：

```
現在執行 docs/07-roadmap.md 的 Phase 0，嚴格遵守 CLAUDE.md 與 docs/08-workflow.md。
請依序：
1. 建立 store-system/ 的 monorepo 骨架（backend/ frontend/ hardware-agent/、
   docker-compose.yml、docs/ 已存在），不可拆成多個 repo。
2. 鎖定並「實際安裝」相依套件（後端用 uv，前端用 pnpm），貼出安裝結果。
3. 設定 ruff、mypy(strict)、pytest+coverage（前端 eslint、tsc、測試）為**本機**品質關卡
   （本專案不使用 GitHub CI）。另設定 API 合約管線（docs/11）：後端匯出 openapi.json、
   前端 pnpm gen:api 生成型別、lib/api.ts 型別化 client，並提供「本機合約漂移檢查」
   （生成後比對版控、有 diff 即失敗）。提供一支本機檢查彙整指令（例 make check）一次跑完四道門 + 合約漂移。
4. 寫一個最小的會通過的測試，實際執行並貼出 pytest/ruff/mypy 的真實輸出。
5. 防呆驗證：故意寫一段 import 不存在模組的程式，證明 ruff/mypy/本機檢查會紅燈，
   貼出紅燈輸出後再移除。
6. 在 .claude/agents/ 放入我提供的 code-reviewer 與 tdd-implementer 兩個 subagent。
7. 版本控制（docs/12）：把專案推到 GitHub 遠端（僅作備份/同步，不跑 CI）、
   確立「禁止直接在 main 開發、一律經 feature 分支」的流程、安裝並登入 gh CLI（選用）、
   把 .claude/worktrees/ 加入 .gitignore、確認 Claude Code 版本支援 worktree（v2.1.50+）。
   合併規則：feature 先 rebase 到最新 main → 本機四道門全綠 + code-reviewer APPROVE →
   合併回 main → push origin main。注意：Phase 0 這些建置本身也開在 chore/phase0-scaffold 分支、最後合併進 main。

每一步都要貼出實際指令輸出，不接受「應該會過」。完成後等我確認。
```

> `.claude/agents/code-reviewer.md` 與 `.claude/agents/tdd-implementer.md` 我已附在文件包，直接複製進專案即可。

---

## 階段 3：逐任務執行（TDD + 並行 + 強制審查）

之後每個 Phase / 任務都用同一套指令模板。**序列化處理會碰共用檔的任務；只並行真正獨立的葉節點任務。**

### 3a. 單一任務（序列，適用核心/共用部分）

```
實作【任務名稱，例：contacts 模組的 PII 加密與遮罩】。
先從最新 main 開分支 feat/<scope>-<簡述>（git pull --ff-only 後再開；勿在 main 上做）。
嚴格遵守 CLAUDE.md（分層、store_id、PII、Decimal、import 置頂、版本控制）與 docs/08 的 TDD 迴圈：
1. 先寫會失敗的測試（涵蓋 docs/06 相關不變量），跑給我看紅燈。
2. 寫最小實作讓它通過。
3. 合併前先 git fetch && git rebase origin/main。
4. 跑本機四道門（ruff/mypy、pytest+覆蓋率、前端 eslint/tsc/測試、合約漂移）全綠，貼出真實輸出。
5. 用 code-reviewer subagent 審查 diff，對照 docs/08 清單，把問題修掉直到 APPROVE。
6. 先 push feature 分支；合併回 main（快轉或 squash）後 push origin main、刪分支。
   列出改了哪些檔、檢查清單結果與各檢查的綠燈輸出（不需我核准）。
```

### 3b. 並行多個獨立任務（用 subagent）

當階段 1 已標出「彼此獨立、不碰共用檔」的任務時：

```
以下任務彼此獨立、不共用檔案（未動 migration / shared enums / 同一模組），
請用 tdd-implementer subagent 以 worktree 隔離「並行」實作，各自從最新 main 開 feat 分支、
遵守 CLAUDE.md 與 docs/08 的 TDD 迴圈、各自跑出本機綠燈：
- 任務 A（feat/...）：……
- 任務 B（feat/...）：……
各自完成後用 code-reviewer subagent 審查（回 APPROVE）；合併前各自 git fetch && rebase origin/main，
依相依順序逐一合併進 main（被依賴者先）、每次合併後 push origin main，最後彙整分支/檢查狀態給我。
```

> 注意：並行會增加 token 用量；且若兩個任務都會動到 migration、shared enums 或同一模組，**不要並行**，改序列做。Alembic migration 一律序列產生以免衝突。

### 3c. 每個 Phase 收尾

```
這個 Phase 的所有任務已完成。請：
1. 跑完整本機檢查（lint/type/test/coverage + 合約漂移）與相關 e2e，貼出結果。
2. 用 code-reviewer subagent 對整個 Phase 的變更做一次總體審查，對照
   docs/07 該 Phase 的驗收項目逐條確認達成。
3. 確認可 docker compose up 跑起來（地基之後）。
回報結果，等我確認後再進下一個 Phase。
```

---

## 確保「無幻想、需求真的被實作」的固定要求（每次都要它做到）

- **貼真實輸出**：每步都要 pytest/ruff/mypy/tsc 的實際終端輸出，不接受口頭保證。
- **import 置頂且真實**：ruff(E402/F401/F821) 會擋；用套件前先確認已安裝、API 存在。
- **需求對照**：每個 Phase 收尾要它對照 `docs/01` 與 `docs/07` 驗收項目逐條打勾，列出「哪個需求由哪些檔/測試實現」。
- **獨立審查**：寫的人不自評，一律經 code-reviewer subagent 回 APPROVE（人工抽查金額/發票/現金/PII 為選配）。
- **小步可回溯**：一任務一 commit；不確定就停下來問你。

## 你每次回覆它時的口頭禪
- 「先給我紅燈再實作。」
- 「貼出 pytest / ruff / mypy 的實際輸出。」
- 「跑 code-reviewer 審查這個 diff，對照 docs/08 清單。」
- 「列出這步對應 docs/01 的哪個需求。」
- 「會碰共用檔的不要並行。」