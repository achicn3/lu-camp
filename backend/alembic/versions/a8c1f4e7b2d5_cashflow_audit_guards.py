"""cashflow audit: immutable ledgers, idempotency, and settlement guards

Revision ID: a8c1f4e7b2d5
Revises: f9d3b7a1c5e8
Create Date: 2026-08-24 20:00:00.000000

All trigger SQL is frozen in this revision.  Do not import model constants here: an
already-applied revision must never change behavior when application code changes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a8c1f4e7b2d5"
down_revision: str | Sequence[str] | None = "f9d3b7a1c5e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CASH_IMMUTABLE_FUNCTION = """
CREATE OR REPLACE FUNCTION cash_movement_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'cash_movements is insert-only: UPDATE/DELETE forbidden';
END;
$$ LANGUAGE plpgsql
"""

_CASH_IMMUTABLE_TRIGGER = """
CREATE TRIGGER trg_cash_movement_immutable
BEFORE UPDATE OR DELETE ON cash_movements
FOR EACH ROW EXECUTE FUNCTION cash_movement_immutable()
"""

_STORE_CREDIT_TRIGGER_NAMES = (
    "trg_store_credit_ledger_immutable",
    "trg_store_credit_reversal_guard",
    "trg_store_credit_credit_guard",
    "trg_store_credit_balance_chain_guard",
    "trg_store_credit_cache_sync",
)

_STORE_CREDIT_DDL: tuple[str, ...] = (
    """
CREATE OR REPLACE FUNCTION store_credit_ledger_immutable() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'store_credit_ledger 為 insert-only（ADR-012）：禁止 UPDATE/DELETE';
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_store_credit_ledger_immutable
BEFORE UPDATE OR DELETE ON store_credit_ledger
FOR EACH ROW EXECUTE FUNCTION store_credit_ledger_immutable()
""",
    """
CREATE OR REPLACE FUNCTION store_credit_reversal_guard() RETURNS trigger AS $$
DECLARE
  original RECORD;
BEGIN
  IF NEW.reversal_of_id IS NULL THEN
    RETURN NEW;
  END IF;
  SELECT entry_type, signed_amount, source_type, source_id INTO original
    FROM store_credit_ledger WHERE id = NEW.reversal_of_id;
  IF original.entry_type = 'REVERSAL' THEN
    RAISE EXCEPTION '沖正列不可再被沖正';
  END IF;
  IF NEW.signed_amount <> -original.signed_amount THEN
    RAISE EXCEPTION '沖正金額必須為原列負值';
  END IF;
  IF NEW.source_type = 'SALE_VOID'
     AND (original.entry_type <> 'DEBIT' OR original.source_type <> 'SALE') THEN
    RAISE EXCEPTION 'SALE_VOID 只能沖 DEBIT/SALE 列';
  END IF;
  IF NEW.source_type = 'ACQUISITION_ROLLBACK'
     AND (original.entry_type <> 'CREDIT' OR original.source_type <> 'ACQUISITION') THEN
    RAISE EXCEPTION 'ACQUISITION_ROLLBACK 只能沖 CREDIT/ACQUISITION 列';
  END IF;
  IF NEW.source_id <> original.source_id THEN
    RAISE EXCEPTION '沖正 source_id 必須等於原列 source_id';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_store_credit_reversal_guard
BEFORE INSERT ON store_credit_ledger
FOR EACH ROW EXECUTE FUNCTION store_credit_reversal_guard()
""",
    """
CREATE OR REPLACE FUNCTION store_credit_credit_guard() RETURNS trigger AS $$
BEGIN
  IF NEW.entry_type <> 'CREDIT' THEN
    RETURN NEW;
  END IF;
  IF NEW.cash_equivalent <= 0 THEN
    RAISE EXCEPTION 'CREDIT 現金等值必須為正';
  END IF;
  IF NEW.premium_rate_applied < 0 OR NEW.premium_rate_applied > 0.2000 THEN
    RAISE EXCEPTION 'CREDIT 溢價率超出政策界線';
  END IF;
  IF NEW.signed_amount <> ROUND(NEW.cash_equivalent * (1 + NEW.premium_rate_applied)) THEN
    RAISE EXCEPTION 'CREDIT 實發額必須等於 round(現金等值 × (1+溢價率))';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_store_credit_credit_guard
BEFORE INSERT ON store_credit_ledger
FOR EACH ROW EXECUTE FUNCTION store_credit_credit_guard()
""",
    """
CREATE OR REPLACE FUNCTION store_credit_balance_chain_guard() RETURNS trigger AS $$
DECLARE
  prior NUMERIC;
BEGIN
  PERFORM 1 FROM store_credit_accounts
    WHERE store_id = NEW.store_id AND contact_id = NEW.contact_id
    FOR UPDATE;
  SELECT COALESCE(SUM(signed_amount), 0) INTO prior
    FROM store_credit_ledger
    WHERE store_id = NEW.store_id AND contact_id = NEW.contact_id;
  IF NEW.balance_after <> prior + NEW.signed_amount THEN
    RAISE EXCEPTION 'balance_after 必須等於滾動和（前和＋本列）';
  END IF;
  IF EXISTS (
    SELECT 1 FROM store_credit_ledger
     WHERE store_id = NEW.store_id AND contact_id = NEW.contact_id
       AND id >= NEW.id
  ) THEN
    RAISE EXCEPTION '帳本只能尾插：id 必須大於該帳戶既有最大 id';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_store_credit_balance_chain_guard
BEFORE INSERT ON store_credit_ledger
FOR EACH ROW EXECUTE FUNCTION store_credit_balance_chain_guard()
""",
    """
CREATE OR REPLACE FUNCTION store_credit_cache_sync() RETURNS trigger AS $$
BEGIN
  UPDATE store_credit_accounts
     SET balance = NEW.balance_after,
         version = version + 1,
         updated_at = now()
   WHERE store_id = NEW.store_id AND contact_id = NEW.contact_id;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
""",
    """
CREATE TRIGGER trg_store_credit_cache_sync
AFTER INSERT ON store_credit_ledger
FOR EACH ROW EXECUTE FUNCTION store_credit_cache_sync()
""",
)


def _add_not_valid_check(table: str, name: str, expression: str) -> None:
    """Protect all new writes without rewriting or silently changing historical ledger rows."""
    op.execute(
        sa.text(f'ALTER TABLE {table} ADD CONSTRAINT "{name}" CHECK ({expression}) NOT VALID')
    )


def upgrade() -> None:
    op.add_column(
        "linepay_refund_attempts", sa.Column("recovery_kind", sa.String(20), nullable=True)
    )
    op.add_column(
        "linepay_refund_attempts", sa.Column("recovery_payload", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "linepay_refund_attempts",
        sa.Column("recovery_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "linepay_refund_attempts", sa.Column("recovery_error", sa.String(500), nullable=True)
    )
    op.add_column(
        "linepay_refund_attempts",
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("cash_movements", sa.Column("idempotency_key", sa.String(80), nullable=True))
    op.add_column(
        "cash_movements", sa.Column("idempotency_fingerprint", sa.String(64), nullable=True)
    )
    op.create_index(
        "uq_cash_movements_store_idempotency_key",
        "cash_movements",
        ["store_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    _add_not_valid_check("cash_movements", "ck_cash_movement_amount_nonzero", "amount <> 0")
    _add_not_valid_check(
        "cash_movements",
        "ck_cash_movement_system_amount_positive",
        "type = 'MANUAL_ADJUST' OR amount > 0",
    )
    _add_not_valid_check(
        "cash_movements",
        "ck_cash_movement_idempotency_pair",
        "(idempotency_key IS NULL) = (idempotency_fingerprint IS NULL)",
    )
    _add_not_valid_check(
        "cash_movements",
        "ck_cash_movement_manual_fields",
        "type <> 'MANUAL_ADJUST' OR"
        " (note IS NOT NULL AND btrim(note) <> ''"
        " AND idempotency_key IS NOT NULL"
        " AND char_length(idempotency_fingerprint) = 64)",
    )
    _add_not_valid_check(
        "cash_movements",
        "ck_cash_movement_system_fields",
        "type = 'MANUAL_ADJUST' OR"
        " (note IS NULL AND idempotency_key IS NULL AND idempotency_fingerprint IS NULL)",
    )

    _add_not_valid_check(
        "consignment_settlements",
        "ck_consignment_settlement_gross_positive",
        "gross > 0",
    )
    _add_not_valid_check(
        "consignment_settlements",
        "ck_consignment_settlement_commission_pct",
        "commission_pct BETWEEN 0 AND 100",
    )
    _add_not_valid_check(
        "consignment_settlements",
        "ck_consignment_settlement_amounts_nonnegative",
        "commission_amount >= 0 AND payout_amount >= 0",
    )
    _add_not_valid_check(
        "consignment_settlements",
        "ck_consignment_settlement_amounts_balance",
        "commission_amount + payout_amount = gross",
    )

    op.execute(_CASH_IMMUTABLE_FUNCTION)
    op.execute(_CASH_IMMUTABLE_TRIGGER)

    # Converge every deployed database on this frozen set, regardless of what the old
    # live-import migration happened to install when it ran.
    for trigger_name in _STORE_CREDIT_TRIGGER_NAMES:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON store_credit_ledger")
    for ddl in _STORE_CREDIT_DDL:
        op.execute(ddl)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_cash_movement_immutable ON cash_movements")
    op.execute("DROP FUNCTION IF EXISTS cash_movement_immutable()")

    for name in (
        "ck_consignment_settlement_amounts_balance",
        "ck_consignment_settlement_amounts_nonnegative",
        "ck_consignment_settlement_commission_pct",
        "ck_consignment_settlement_gross_positive",
    ):
        op.drop_constraint(name, "consignment_settlements", type_="check")
    for name in (
        "ck_cash_movement_system_fields",
        "ck_cash_movement_manual_fields",
        "ck_cash_movement_idempotency_pair",
        "ck_cash_movement_system_amount_positive",
        "ck_cash_movement_amount_nonzero",
    ):
        op.drop_constraint(name, "cash_movements", type_="check")
    op.drop_index("uq_cash_movements_store_idempotency_key", table_name="cash_movements")
    op.drop_column("cash_movements", "idempotency_fingerprint")
    op.drop_column("cash_movements", "idempotency_key")

    op.drop_column("linepay_refund_attempts", "recovered_at")
    op.drop_column("linepay_refund_attempts", "recovery_error")
    op.drop_column("linepay_refund_attempts", "recovery_attempts")
    op.drop_column("linepay_refund_attempts", "recovery_payload")
    op.drop_column("linepay_refund_attempts", "recovery_kind")

    # Store-credit trigger definitions already belong to the prior schema revision.  Keep
    # the frozen, equivalent protection installed on downgrade instead of weakening a live
    # financial ledger merely because this convergence revision is rolled back.
