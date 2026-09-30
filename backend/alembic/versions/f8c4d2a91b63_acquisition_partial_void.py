"""Persist item-level acquisition voids and bounded partial credit reversals."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f8c4d2a91b63"
down_revision = "b7e2c4d9f1a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "acquisition_voids",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("acquisition_id", sa.Integer(), sa.ForeignKey("acquisitions.id"), nullable=False),
        sa.Column("item_ids", postgresql.JSONB(), nullable=False),
        sa.Column("reversed_cost", sa.Numeric(12, 0), nullable=False),
        sa.Column("reversed_cash", sa.Numeric(12, 0), nullable=False),
        sa.Column("reversed_credit_equivalent", sa.Numeric(12, 0), nullable=False),
        sa.Column("reversed_credit", sa.Numeric(12, 0), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "reversed_cost >= 0 AND reversed_cash >= 0 AND reversed_credit >= 0 "
            "AND reversed_credit_equivalent >= 0",
            name="ck_acquisition_voids_nonneg",
        ),
        sa.CheckConstraint(
            "reversed_cash + reversed_credit_equivalent = reversed_cost",
            name="ck_acquisition_voids_split",
        ),
    )
    for column in ("store_id", "acquisition_id"):
        op.create_index(f"ix_acquisition_voids_{column}", "acquisition_voids", [column])
    op.add_column(
        "store_credit_ledger",
        sa.Column(
            "acquisition_void_id",
            sa.Integer(),
            sa.ForeignKey("acquisition_voids.id"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_scl_acquisition_void", "store_credit_ledger", ["acquisition_void_id"]
    )
    op.create_check_constraint(
        "ck_scl_acquisition_void_source",
        "store_credit_ledger",
        "acquisition_void_id IS NULL OR (entry_type = 'REVERSAL' "
            "AND source_type = 'ACQUISITION_ROLLBACK')",
    )
    op.drop_constraint("uq_store_credit_ledger_source", "store_credit_ledger", type_="unique")
    op.create_index(
        "uq_store_credit_ledger_source",
        "store_credit_ledger",
        ["store_id", "source_type", "source_id", "entry_type"],
        unique=True,
        postgresql_where=sa.text("acquisition_void_id IS NULL"),
    )
    op.drop_index("uq_store_credit_ledger_reversal_of", table_name="store_credit_ledger")
    op.create_index(
        "uq_store_credit_ledger_reversal_of",
        "store_credit_ledger",
        ["reversal_of_id"],
        unique=True,
        postgresql_where=sa.text("reversal_of_id IS NOT NULL AND acquisition_void_id IS NULL"),
    )
    op.execute(NEW_GUARD)


def downgrade() -> None:
    # 有選品作廢歷史時舊 schema 無法表達，不可刪帳來強行降版。
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM acquisition_voids)")):
        raise RuntimeError("存在選品作廢紀錄，不可降版")
    op.execute(OLD_GUARD)
    op.drop_index("uq_store_credit_ledger_reversal_of", table_name="store_credit_ledger")
    op.create_index(
        "uq_store_credit_ledger_reversal_of",
        "store_credit_ledger",
        ["reversal_of_id"],
        unique=True,
        postgresql_where=sa.text("reversal_of_id IS NOT NULL"),
    )
    op.drop_index("uq_store_credit_ledger_source", table_name="store_credit_ledger")
    op.create_unique_constraint(
        "uq_store_credit_ledger_source",
        "store_credit_ledger",
        ["store_id", "source_type", "source_id", "entry_type"],
    )
    op.drop_constraint("ck_scl_acquisition_void_source", "store_credit_ledger", type_="check")
    op.drop_constraint("uq_scl_acquisition_void", "store_credit_ledger", type_="unique")
    op.drop_column("store_credit_ledger", "acquisition_void_id")
    op.drop_table("acquisition_voids")


NEW_GUARD = """
CREATE OR REPLACE FUNCTION store_credit_reversal_guard() RETURNS trigger AS $$
DECLARE
  original RECORD;
  reversal_record RECORD;
  reversed NUMERIC;
BEGIN
  IF NEW.reversal_of_id IS NULL THEN
    RETURN NEW;
  END IF;
  SELECT entry_type, signed_amount, source_type, source_id INTO original
    FROM store_credit_ledger WHERE id = NEW.reversal_of_id;
  IF original.entry_type = 'REVERSAL' THEN
    RAISE EXCEPTION '沖正列不可再被沖正';
  END IF;
  IF NEW.acquisition_void_id IS NOT NULL THEN
    SELECT v.*, a.contact_id INTO reversal_record FROM acquisition_voids v
      JOIN acquisitions a ON a.id = v.acquisition_id AND a.store_id = v.store_id
      WHERE v.id = NEW.acquisition_void_id;
    IF NOT FOUND OR reversal_record.store_id <> NEW.store_id
       OR reversal_record.contact_id <> NEW.contact_id
       OR reversal_record.acquisition_id <> NEW.source_id
       OR reversal_record.reversed_credit <> -NEW.signed_amount
       OR -reversal_record.reversed_credit_equivalent IS DISTINCT FROM NEW.cash_equivalent THEN
      RAISE EXCEPTION '選品作廢沖回分錄與作廢紀錄不一致';
    END IF;
    PERFORM 1 FROM store_credit_accounts WHERE store_id = NEW.store_id
      AND contact_id = NEW.contact_id FOR UPDATE;
    SELECT COALESCE(-SUM(signed_amount), 0) INTO reversed
      FROM store_credit_ledger WHERE reversal_of_id = NEW.reversal_of_id;
    IF NEW.signed_amount >= 0 OR reversed - NEW.signed_amount > original.signed_amount THEN
      RAISE EXCEPTION '選品作廢累計沖回不可超過原購物金';
    END IF;
  ELSIF EXISTS (SELECT 1 FROM store_credit_ledger WHERE reversal_of_id = NEW.reversal_of_id)
        OR NEW.signed_amount <> -original.signed_amount THEN
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
"""

OLD_GUARD = """
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
"""
