# Current Status Index

> Snapshot: 2026-07-23 (`main`, after store-credit mobile-payment integration). This file is the planning index; source of truth
> remains the current branch diff, migrations, generated OpenAPI, tests, and local quality gates.
> Detailed decisions remain in the numbered specs.

## Branch Context

| Item | Current note |
|---|---|
| Main branch baseline | Phases 0–6, Amego e-invoice, mobile payments, kiosk signing, Phase 7 backup/restore, and Taiwan-time consistency are merged into `main`. Production deployment/observability remains a separate readiness phase. |
| Active product phase branch | None. `feat/pos-store-credit-mobile-payments` is merged in this snapshot. Old v0.0.1 hardening branches remain abandoned. |
| Review gate | Follow `CLAUDE.md`: local gates first, then Codex review for the whole task; UI changes also require real Playwright and screenshots. |
| Historical status | `PHASE0_STATUS.md`, docs/25–29, and implementation plans preserve point-in-time evidence. Their dated measurements are not the current implementation matrix. |

## Implementation Matrix

| Area | Current status | Planning source |
|---|---|---|
| Foundation / local gates | Implemented. `make check` / `check.sh` / `check.ps1` are the local gate entry points. The repository still has a known 62-file Ruff formatter baseline; lint/type/test gates pass. | `CLAUDE.md`, docs/06, docs/08, docs/11, docs/12 |
| Auth / settings / store / audit / money | Implemented. D-4 is resolved: every authenticated request reloads the active user and current role/store from DB. Never-expire login is therefore revocable through DB state. | `backend/app/core/deps.py`, `backend/tests/integration/test_deps_revalidation.py` |
| Contacts / member center | Implemented: create/edit/validation, overview, purchases, consignments, acquisition sources, balances, pagination, and masked PII paths. | docs/17, contacts module and frontend pages |
| Inventory / acquisition | Implemented: serialized, bulk, and general products; pricing, stock movements, seller lookup, kiosk affidavit binding, payout methods, and manager-controlled void. | docs/01, docs/13, docs/23, inventory/acquisition modules |
| Cash drawer | Implemented: opening/closing, adjustments with reason/history, fixed monthly cash expense settings, race protection, and Taiwan-day reporting. | docs/01, docs/19, cashdrawer module |
| Sales / POS / tenders | Implemented: mixed carts, discounts, idempotency, cash/store-credit/Taiwan Pay/LINE Pay, store-credit plus exactly one remaining channel, signing, tender-symmetric void, item/whole returns, and receipt transaction/tender details. Multi-external payment combinations are rejected. Desktop/mobile Playwright flow passes. | docs/10, docs/16, docs/21, docs/23, docs/30 |
| Store credit / points | SC-1–SC-5 implemented, including append-only `REFUND/SALE_RETURN`, cumulative store-credit-first refunds, `return_tenders`, DB consistency guards and net report reversal. Return point clawback remains proportional. | docs/16, ADR-012, storecredit tests |
| Kiosk signing / evidence | K1–K6 implemented: kiosk role, affidavit/SCU/ACK tasks, PNG validation, acquisition/POS binding, printable evidence, and staff evidence viewer/direct links. | docs/23, signing module and smoke tests |
| Consignment / returns | Implemented: settlement creation/payment, reclaim/cancel handling, inventory restoration, idempotent partial returns, LINE Pay refunds, invoice allowance flow, and sales-page return UI. | docs/07, docs/19, docs/24, docs/30 |
| Purchasing / stocktake | Implemented: suppliers, PO draft/submit/cancel, partial receipt, invoice capture, low-stock/incoming quantities, stocktake adjustment, first-time general-product creation, and UI smoke coverage. | docs/07, purchasing/stocktake modules |
| Reports / campaigns / menu | Phase 6 reports, CSV/XLSX, campaign C1–C4, and menu M1–M4 are implemented. Main accounting reports subtract returns and payment fees. | docs/19, docs/21, reports/campaigns/menu modules |
| E-invoice | Amego B2B/B2C issue/void/allowance/query, persistent delivery state, POS fields, and EPSON proof print are implemented. On 2026-07-23 a real test F0401 plus two G0401 allowances reached query status 99 and were confirmed in the Amego backoffice. | docs/24, einvoice module, `einvoice-smoke.mjs` |
| Backup / restore | Implemented: due-driven encrypted full-DB R2 backups, dashboard, retention, health alerts, guarded restore to a throwaway DB, verification, switch script, and 23/23 functional restore drill. | docs/28, docs/31, backup module |
| Time contract | Implemented: aware UTC instants in DB/API; Taiwan calendar dates, report buckets, exports, browser display, and automation scripts; naive API datetimes fail closed. | docs/04, docs/10, docs/11, `app/core/time.py`, `frontend/lib/datetime.ts` |

## Remaining Decisions and External Readiness

- **G3 accounting decision**: an accountant must confirm stored-value classification, expiry/performance
  guarantee, premium recognition, and whether store-credit payment changes invoice timing/amount. Current
  code treats store credit as a payment instrument and invoices the full sale amount.
- **Production credentials and hardware**: production Amego/LINE Pay credentials, R2 secrets and
  off-site passphrase custody, fixed networking, and final in-store printer/drawer checks are deployment
  inputs, not missing application modules.
- **Deployment/observability**: production service supervision, restart policy, log rotation, aggregate
  health/alerts, and the final one-command deployment path from Phase 7 still require an environment-specific
  implementation and rehearsal.
- **Accepted model/design notes**: D-3 sale-void versus invoice-void separation remains intentionally
  deferred; docs/30 records the single-PC LINE Pay orphan-payment residual; mounted manager pages do not
  proactively redirect on a live demotion, while every backend request still revalidates authorization.
- **Accepted analytics limits**: SC-5 suggestion `period_margin` and brand/category insights do not
  subtract returns; main accounting reports do. This was explicitly accepted and quantified at about
  0.05% on the 200-day simulation dataset.
- **Superseded branch**: the older `feat/pos-store-credit-amount` branch is superseded by the merged
  store-credit mobile-payment flow and should not be merged separately.
- **Future product scope**: category specification templates in docs/13 remain planning-only.

## Planning Guidance

- Use this matrix plus current code/tests before scheduling work; several Claude memories and dated plans
  predate later merges and must not be treated as authoritative status.
- The next product-facing phase after this documentation reconciliation is deployment readiness:
  production secrets, hardware/network validation, backup restore rehearsal, and release checklist.
- Keep abandoned v0.0.1 hardening branches isolated. If dependency/security-header work is reprioritized,
  restart it from current `main` instead of merging the old stacked branches.
