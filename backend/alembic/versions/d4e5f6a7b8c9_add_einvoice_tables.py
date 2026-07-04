"""add einvoice tables (T13 infra: invoices / allowances / upload queue / result events)

電子發票基礎建設（cert-independent）：本地發票紀錄、折讓、Turnkey 上傳外送佇列、回執事件。
每張表帶 store_id；金額 NUMERIC(12,0) 整數元；列舉存 VARCHAR + CHECK（native_enum=False）。
核心不變量以 DB 約束守護：一筆銷售至多一張發票、字軌號碼同店唯一、佇列目標 XOR。

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=30, create_constraint=True)


# sales.invoice_status（native_enum=False 的 CHECK 名為 'saleinvoicestatus'）擴充/回退值。
_SALE_INVOICE_STATUS_OLD = "'NOT_ISSUED', 'ISSUED', 'VOID', 'ALLOWANCE'"
_SALE_INVOICE_STATUS_NEW = (
    "'NOT_ISSUED', 'PENDING_ISSUE', 'ISSUED', 'PENDING_ALLOWANCE', 'ALLOWANCE', 'VOID'"
)


def upgrade() -> None:
    """Upgrade schema."""
    # sales.invoice_status 擴充 PENDING_ISSUE（結帳排入電子發票、尚未平台核可的誠實中間態）。
    op.drop_constraint("saleinvoicestatus", "sales", type_="check")
    op.create_check_constraint(
        "saleinvoicestatus", "sales", f"invoice_status IN ({_SALE_INVOICE_STATUS_NEW})"
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("sale_id", sa.Integer(), nullable=False),
        sa.Column("invoice_type", _enum("B2C", "B2B", name="invoicetype"), nullable=False),
        sa.Column("invoice_no", sa.String(length=16), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("invoice_time", sa.String(length=8), nullable=True),
        sa.Column("random_number", sa.String(length=4), nullable=True),
        sa.Column("buyer_tax_id", sa.String(length=8), nullable=True),
        sa.Column("buyer_name", sa.String(length=60), nullable=True),
        sa.Column("carrier_type", sa.String(length=10), nullable=True),
        sa.Column("carrier_id", sa.String(length=400), nullable=True),
        sa.Column("donate_mark", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("npoban", sa.String(length=7), nullable=True),
        sa.Column("print_mark", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("net", sa.Numeric(precision=12, scale=0), nullable=False),
        sa.Column("tax", sa.Numeric(precision=12, scale=0), nullable=False),
        sa.Column("total", sa.Numeric(precision=12, scale=0), nullable=False),
        sa.Column(
            "status",
            _enum("PENDING", "ISSUED", "VOID_PENDING", "VOID", "ALLOWANCE", name="invoicestatus"),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("sale_id", name="uq_invoices_sale"),
        sa.UniqueConstraint("id", "store_id", name="uq_invoices_id_store"),
        sa.ForeignKeyConstraint(
            ["sale_id", "store_id"],
            ["sales.id", "sales.store_id"],
            name="fk_invoices_sale_tenant",
        ),
        sa.CheckConstraint("total > 0", name="ck_invoices_total_positive"),
        sa.CheckConstraint("net >= 0 AND tax >= 0", name="ck_invoices_amounts_nonneg"),
        sa.CheckConstraint("net + tax = total", name="ck_invoices_net_tax_total"),
        sa.CheckConstraint(
            "(donate_mark = false AND npoban IS NULL)"
            " OR (donate_mark = true AND npoban IS NOT NULL)",
            name="ck_invoices_donate_npoban",
        ),
        sa.CheckConstraint(
            "(invoice_type = 'B2B' AND buyer_tax_id IS NOT NULL)"
            " OR (invoice_type = 'B2C' AND buyer_tax_id IS NULL)",
            name="ck_invoices_buyer_tax_id",
        ),
    )
    op.create_index("ix_invoices_store_id", "invoices", ["store_id"])
    op.create_index("ix_invoices_sale_id", "invoices", ["sale_id"])
    op.create_index(
        "uq_invoices_store_invoice_no",
        "invoices",
        ["store_id", "invoice_no"],
        unique=True,
        postgresql_where=sa.text("invoice_no IS NOT NULL"),
    )

    op.create_table(
        "invoice_allowances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("return_id", sa.Integer(), nullable=True),
        sa.Column("allowance_no", sa.String(length=16), nullable=True),
        sa.Column("net", sa.Numeric(precision=12, scale=0), nullable=False),
        sa.Column("tax", sa.Numeric(precision=12, scale=0), nullable=False),
        sa.Column("total", sa.Numeric(precision=12, scale=0), nullable=False),
        sa.Column("voided", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "store_id", name="uq_invoice_allowances_id_store"),
        sa.ForeignKeyConstraint(
            ["invoice_id", "store_id"],
            ["invoices.id", "invoices.store_id"],
            name="fk_invoice_allowances_invoice_tenant",
        ),
        sa.CheckConstraint("total > 0", name="ck_invoice_allowances_total_positive"),
        sa.CheckConstraint("net >= 0 AND tax >= 0", name="ck_invoice_allowances_amounts_nonneg"),
        sa.CheckConstraint("net + tax = total", name="ck_invoice_allowances_net_tax_total"),
    )
    op.create_index("ix_invoice_allowances_store_id", "invoice_allowances", ["store_id"])
    op.create_index("ix_invoice_allowances_invoice_id", "invoice_allowances", ["invoice_id"])
    op.create_index(
        "uq_invoice_allowances_store_no",
        "invoice_allowances",
        ["store_id", "allowance_no"],
        unique=True,
        postgresql_where=sa.text("allowance_no IS NOT NULL"),
    )
    op.create_index(
        "uq_invoice_allowances_return",
        "invoice_allowances",
        ["store_id", "return_id"],
        unique=True,
        postgresql_where=sa.text("return_id IS NOT NULL"),
    )

    op.create_table(
        "einvoice_upload_queue",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "action", _enum("ISSUE", "VOID", "ALLOWANCE", name="einvoiceaction"), nullable=False
        ),
        sa.Column(
            "message_type",
            _enum("F0401", "F0501", "F0701", "G0401", "G0501", name="einvoicemessagetype"),
            nullable=False,
        ),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("allowance_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            _enum("PENDING", "UPLOADED", "FAILED", "CANCELLED", name="uploadstatus"),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("xml_path", sa.String(length=500), nullable=True),
        sa.Column("xml_sha256", sa.String(length=64), nullable=True),
        sa.Column("dropped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "store_id", name="uq_einvoice_queue_id_store"),
        sa.ForeignKeyConstraint(
            ["invoice_id", "store_id"],
            ["invoices.id", "invoices.store_id"],
            name="fk_einvoice_queue_invoice_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["allowance_id", "store_id"],
            ["invoice_allowances.id", "invoice_allowances.store_id"],
            name="fk_einvoice_queue_allowance_tenant",
        ),
        sa.CheckConstraint(
            "(invoice_id IS NOT NULL) <> (allowance_id IS NOT NULL)",
            name="ck_einvoice_queue_target_xor",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_einvoice_queue_attempts_nonneg"),
    )
    op.create_index("ix_einvoice_upload_queue_store_id", "einvoice_upload_queue", ["store_id"])
    op.create_index("ix_einvoice_upload_queue_invoice_id", "einvoice_upload_queue", ["invoice_id"])
    op.create_index(
        "ix_einvoice_upload_queue_allowance_id", "einvoice_upload_queue", ["allowance_id"]
    )
    op.create_index("ix_einvoice_upload_queue_status", "einvoice_upload_queue", ["status"])

    op.create_table(
        "einvoice_result_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("queue_id", sa.Integer(), nullable=False),
        sa.Column("result_kind", sa.String(length=20), nullable=False),
        sa.Column("status_code", sa.String(length=20), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("source_ref", sa.String(length=200), nullable=True),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["queue_id", "store_id"],
            ["einvoice_upload_queue.id", "einvoice_upload_queue.store_id"],
            name="fk_einvoice_result_queue_tenant",
        ),
    )
    op.create_index("ix_einvoice_result_events_store_id", "einvoice_result_events", ["store_id"])
    op.create_index("ix_einvoice_result_events_queue_id", "einvoice_result_events", ["queue_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_einvoice_result_events_queue_id", "einvoice_result_events")
    op.drop_index("ix_einvoice_result_events_store_id", "einvoice_result_events")
    op.drop_table("einvoice_result_events")

    op.drop_index("ix_einvoice_upload_queue_status", "einvoice_upload_queue")
    op.drop_index("ix_einvoice_upload_queue_allowance_id", "einvoice_upload_queue")
    op.drop_index("ix_einvoice_upload_queue_invoice_id", "einvoice_upload_queue")
    op.drop_index("ix_einvoice_upload_queue_store_id", "einvoice_upload_queue")
    op.drop_table("einvoice_upload_queue")

    op.drop_index("uq_invoice_allowances_return", "invoice_allowances")
    op.drop_index("uq_invoice_allowances_store_no", "invoice_allowances")
    op.drop_index("ix_invoice_allowances_invoice_id", "invoice_allowances")
    op.drop_index("ix_invoice_allowances_store_id", "invoice_allowances")
    op.drop_table("invoice_allowances")

    op.drop_index("uq_invoices_store_invoice_no", "invoices")
    op.drop_index("ix_invoices_sale_id", "invoices")
    op.drop_index("ix_invoices_store_id", "invoices")
    op.drop_table("invoices")

    # 回退 sales.invoice_status CHECK（移除 PENDING_ISSUE）。
    op.drop_constraint("saleinvoicestatus", "sales", type_="check")
    op.create_check_constraint(
        "saleinvoicestatus", "sales", f"invoice_status IN ({_SALE_INVOICE_STATUS_OLD})"
    )
