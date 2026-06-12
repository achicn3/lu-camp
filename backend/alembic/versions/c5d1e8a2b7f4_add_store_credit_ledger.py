"""add store credit ledger and accounts

Revision ID: c5d1e8a2b7f4
Revises: 8b4c7e2f9a31
Create Date: 2026-06-12 10:00:00.000000

購物金核心（SC-1，docs/16 §1、ADR-012）：insert-only 帳本＋帳戶快取。
trigger 定義與 models.LEDGER_IMMUTABLE_DDL 同源（避免漂移）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.modules.storecredit.models import LEDGER_IMMUTABLE_DDL, LEDGER_IMMUTABLE_DROP_DDL

# revision identifiers, used by Alembic.
revision: str = "c5d1e8a2b7f4"
down_revision: str | Sequence[str] | None = "8b4c7e2f9a31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENTRY_TYPES = ("CREDIT", "DEBIT", "REVERSAL", "ADJUSTMENT")
_SOURCE_TYPES = ("ACQUISITION", "SALE", "SALE_VOID", "ACQUISITION_ROLLBACK", "MANUAL")


def upgrade() -> None:
    """Upgrade schema."""
    # 供複合 FK 指向：contacts(id, store_id) 唯一（id 為 PK，本約束恆成立、零成本）。
    op.create_unique_constraint("uq_contacts_id_store", "contacts", ["id", "store_id"])
    op.create_table(
        "store_credit_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column(
            "entry_type",
            sa.Enum(*_ENTRY_TYPES, name="storecreditentrytype", native_enum=False, length=30),
            nullable=False,
        ),
        sa.Column("signed_amount", sa.Numeric(12, 0), nullable=False),
        sa.Column("balance_after", sa.Numeric(12, 0), nullable=False),
        sa.Column("cash_equivalent", sa.Numeric(12, 0), nullable=True),
        sa.Column("premium_rate_applied", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "source_type",
            sa.Enum(*_SOURCE_TYPES, name="storecreditsourcetype", native_enum=False, length=30),
            nullable=False,
        ),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("reversal_of_id", sa.Integer(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(80), nullable=True),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "store_id",
            "source_type",
            "source_id",
            "entry_type",
            name="uq_store_credit_ledger_source",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id", "store_id"],
            ["contacts.id", "contacts.store_id"],
            name="fk_store_credit_ledger_contact_store",
        ),
        sa.UniqueConstraint(
            "id", "store_id", "contact_id", name="uq_store_credit_ledger_id_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["reversal_of_id", "store_id", "contact_id"],
            [
                "store_credit_ledger.id",
                "store_credit_ledger.store_id",
                "store_credit_ledger.contact_id",
            ],
            name="fk_store_credit_ledger_reversal_tenant",
        ),
        sa.CheckConstraint("signed_amount <> 0", name="ck_scl_signed_nonzero"),
        sa.CheckConstraint("entry_type <> 'CREDIT' OR signed_amount > 0", name="ck_scl_credit_pos"),
        sa.CheckConstraint("entry_type <> 'DEBIT' OR signed_amount < 0", name="ck_scl_debit_neg"),
        sa.CheckConstraint(
            "(entry_type = 'REVERSAL') = (reversal_of_id IS NOT NULL)",
            name="ck_scl_reversal_ref",
        ),
        sa.CheckConstraint(
            "entry_type <> 'ADJUSTMENT' OR (source_type = 'MANUAL' AND source_id IS NULL)",
            name="ck_scl_adjust_manual",
        ),
        sa.CheckConstraint(
            "entry_type = 'ADJUSTMENT' OR source_id IS NOT NULL",
            name="ck_scl_source_required",
        ),
        sa.CheckConstraint("balance_after >= 0", name="ck_scl_balance_after_nonneg"),
        sa.CheckConstraint(
            "entry_type <> 'CREDIT' OR"
            " (cash_equivalent IS NOT NULL AND premium_rate_applied IS NOT NULL)",
            name="ck_scl_credit_fields",
        ),
        sa.CheckConstraint(
            "entry_type <> 'ADJUSTMENT' OR (reason IS NOT NULL AND idempotency_key IS NOT NULL)",
            name="ck_scl_adjust_fields",
        ),
    )
    op.create_index("ix_store_credit_ledger_store_id", "store_credit_ledger", ["store_id"])
    op.create_index(
        "uq_store_credit_ledger_idem_key",
        "store_credit_ledger",
        ["store_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_store_credit_ledger_reversal_of",
        "store_credit_ledger",
        ["reversal_of_id"],
        unique=True,
        postgresql_where=sa.text("reversal_of_id IS NOT NULL"),
    )
    op.create_index("ix_store_credit_ledger_contact_id", "store_credit_ledger", ["contact_id"])

    op.create_table(
        "store_credit_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("balance", sa.Numeric(12, 0), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("store_id", "contact_id", name="uq_store_credit_accounts_contact"),
        sa.CheckConstraint("balance >= 0", name="ck_sca_balance_nonneg"),
        sa.ForeignKeyConstraint(
            ["contact_id", "store_id"],
            ["contacts.id", "contacts.store_id"],
            name="fk_store_credit_accounts_contact_store",
        ),
    )
    op.create_index("ix_store_credit_accounts_store_id", "store_credit_accounts", ["store_id"])
    op.create_index("ix_store_credit_accounts_contact_id", "store_credit_accounts", ["contact_id"])

    op.create_foreign_key(
        "fk_store_credit_ledger_account",
        "store_credit_ledger",
        "store_credit_accounts",
        ["store_id", "contact_id"],
        ["store_id", "contact_id"],
    )
    for ddl in LEDGER_IMMUTABLE_DDL:
        op.execute(ddl)


def downgrade() -> None:
    """Downgrade schema."""
    for ddl in LEDGER_IMMUTABLE_DROP_DDL:
        op.execute(ddl)
    # ledger 持有指向 accounts 的 FK：必須先刪 ledger（Codex 第七輪 medium，
    # 否則 PostgreSQL 因依賴拒刪、緊急回滾會卡住）。
    op.drop_table("store_credit_ledger")
    op.drop_table("store_credit_accounts")
    op.drop_constraint("uq_contacts_id_store", "contacts", type_="unique")
