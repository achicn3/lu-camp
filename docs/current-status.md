# Current Status Index

> Snapshot for planning only. Source of truth remains the current branch diff, generated OpenAPI,
> migrations, tests, and the local quality gates. Update this file when a phase/task changes state;
> keep detailed design in the numbered specs.

## Branch Context

| Item | Current note |
|---|---|
| Main branch baseline | Contains backend modules for auth/user/store/settings/audit, contacts/member reads, inventory, acquisition, cashdrawer, sales, store credit, reports, plus frontend login/contacts/cash/POS pages. |
| Active known worktree | `feat/acquisition-ui` is checked out at `/home/test/lu-camp-f6` and is not complete. It touches acquisition, inventory, contacts router, migrations, frontend `/acquisition` and `/inventory`, OpenAPI generated files, layout/CSS, and related tests. Avoid overlapping work until it lands or is rebased. |
| Review gate | Follow `CLAUDE.md`: local gates first, then Codex review (`/codex:review --base main` or `/codex:adversarial-review --base main` for high-risk work). |
| Historical status | `PHASE0_STATUS.md` is retained as history only; do not use it as current project state. |

## Implementation Matrix

| Area | Current status | Planning source |
|---|---|---|
| Foundation / local gates | Implemented; use `make check` / `check.sh` / `check.ps1` as the local gate entry points. | `CLAUDE.md`, `docs/06`, `docs/08`, `docs/11`, `docs/12` |
| Auth, settings, store, audit, money | Implemented baseline. D-4 auth hardening remains pending before real money/PII frontend usage. | `docs/deferred-items.md` |
| Contacts / member center | Core contact and member read paths exist. Member-center planning and edge-case requirements live in docs/17. | `docs/17-member-center.md` |
| Inventory / acquisition UX | Active incomplete work on `feat/acquisition-ui`; coordinate through that branch before changing related files. | `docs/10`, `docs/13`, active branch diff |
| Cash drawer | Backend and frontend cash page exist; D-1 race item is resolved. | `docs/deferred-items.md` |
| Sales / POS / tenders | Backend sales, idempotency, void, member points, and store-credit tenders exist; frontend POS page exists. | `docs/07`, `docs/16`, tests |
| Store credit | SC-1 to SC-5 implementation appears represented in code/tests, including reports, settings, suggestion engine, and DB guards. Re-verify with current tests before depending on it. | `docs/16-store-credit.md`, ADR-012, tests |
| Reports | Store-credit reports exist. General Phase 6 financial reports from `docs/04`/`docs/07` are still separate future work unless implemented on another branch. | `docs/04`, `docs/07`, reports module |
| Returns / consignment payout / purchasing / stocktake | Still future or partial unless a branch explicitly implements them. Avoid assuming completion from roadmap order alone. | `docs/07` |
| E-invoice / Turnkey | Deferred to final e-invoice stage. Use `docs/14` as the canonical version/spec research source. | `docs/07`, `docs/14` |

## Low-Conflict Work Guidance

- While `feat/acquisition-ui` is active, avoid acquisition, inventory, contacts router, migrations,
  generated OpenAPI, frontend `/acquisition`, frontend `/inventory`, layout, and global CSS.
- Low-conflict candidates are documentation-only cleanups, isolated deferred-item planning, or
  review-only work.
- D-4 auth hardening is important but cross-cutting; schedule it after the active acquisition UI branch
  lands unless the user explicitly prioritizes it.
