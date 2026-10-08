"""混合付款的購物金：兩種開票方式可在設定切換（ADR-029；店主 2026-10-07／10-08）。

- settings 加 `store_credit_invoice_mode`（DEDUCT 扣掉購物金後開，預設／ALLOWANCE 整筆開＋
  購物金折讓）；invoices 加 `store_credit_mode`（結帳當下用哪一種，沒用購物金為 NULL）。

- invoice_allowances 加 `source`（RETURN／STORE_CREDIT，既有列一律是退貨）、`void_requested_at`；
  一張發票至多一張購物金折讓（部分唯一索引）；購物金折讓不掛退貨單。
- einvoice_upload_queue.action 加 `ALLOWANCE_VOID`（G0501 作廢折讓）。

Revision ID: e3a9c5d1f7b2
Revises: d8f2a4c6e901
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "e3a9c5d1f7b2"
down_revision = "d8f2a4c6e901"
branch_labels = None
depends_on = None

_MODES = ("DEDUCT", "ALLOWANCE")
_ACTION_CHECK = "einvoiceaction"
_ACTIONS_OLD = "'ISSUE', 'VOID', 'ALLOWANCE'"
_ACTIONS_NEW = "'ISSUE', 'VOID', 'ALLOWANCE', 'ALLOWANCE_VOID'"


def _mode_enum() -> sa.Enum:
    return sa.Enum(
        *_MODES, name="storecreditinvoicemode", native_enum=False, length=30, create_constraint=True
    )


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column(
            "store_credit_invoice_mode", _mode_enum(), nullable=False, server_default="DEDUCT"
        ),
    )
    op.add_column("invoices", sa.Column("store_credit_mode", _mode_enum(), nullable=True))
    op.add_column(
        "invoice_allowances",
        sa.Column(
            "source",
            sa.Enum(
                "RETURN",
                "STORE_CREDIT",
                name="invoiceallowancesource",
                native_enum=False,
                length=30,
                create_constraint=True,
            ),
            nullable=False,
            server_default="RETURN",
        ),
    )
    op.add_column(
        "invoice_allowances",
        sa.Column("void_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_invoice_allowances_store_credit",
        "invoice_allowances",
        ["store_id", "invoice_id"],
        unique=True,
        postgresql_where=sa.text("source = 'STORE_CREDIT'"),
    )
    op.create_check_constraint(
        "ck_invoice_allowances_store_credit_no_return",
        "invoice_allowances",
        "source <> 'STORE_CREDIT' OR return_id IS NULL",
    )
    op.drop_constraint(_ACTION_CHECK, "einvoice_upload_queue", type_="check")
    op.create_check_constraint(
        _ACTION_CHECK, "einvoice_upload_queue", f"action IN ({_ACTIONS_NEW})"
    )


def abort_if_store_credit_allowances_exist(conn: Connection) -> None:
    """降版會失去「購物金折讓／作廢折讓」的語意；已經有這類資料就中止，不替它們亂改。"""
    allowances = conn.execute(
        sa.text("SELECT count(*) FROM invoice_allowances WHERE source = 'STORE_CREDIT'")
    ).scalar_one()
    voids = conn.execute(
        sa.text("SELECT count(*) FROM einvoice_upload_queue WHERE action = 'ALLOWANCE_VOID'")
    ).scalar_one()
    # 扣掉購物金後開的發票（總額小於成交總額）：舊程式會把它的退貨折讓算錯。
    modes = conn.execute(
        sa.text("SELECT count(*) FROM invoices WHERE store_credit_mode IS NOT NULL")
    ).scalar_one()
    if allowances or voids or modes:
        raise RuntimeError(
            f"拒絕降版：已有 {allowances} 張購物金折讓、{voids} 筆作廢折讓佇列、"
            f"{modes} 張混合付款發票；只退程式、不要降資料庫"
        )


def downgrade() -> None:
    abort_if_store_credit_allowances_exist(op.get_bind())
    op.drop_constraint(_ACTION_CHECK, "einvoice_upload_queue", type_="check")
    op.create_check_constraint(
        _ACTION_CHECK, "einvoice_upload_queue", f"action IN ({_ACTIONS_OLD})"
    )
    op.drop_constraint(
        "ck_invoice_allowances_store_credit_no_return", "invoice_allowances", type_="check"
    )
    op.drop_index("uq_invoice_allowances_store_credit", table_name="invoice_allowances")
    op.drop_column("invoice_allowances", "void_requested_at")
    op.drop_column("invoice_allowances", "source")
    op.execute("ALTER TABLE invoice_allowances DROP CONSTRAINT IF EXISTS invoiceallowancesource")
    op.drop_column("invoices", "store_credit_mode")
    op.drop_column("settings", "store_credit_invoice_mode")
    op.execute("ALTER TABLE invoices DROP CONSTRAINT IF EXISTS storecreditinvoicemode")
    op.execute("ALTER TABLE settings DROP CONSTRAINT IF EXISTS storecreditinvoicemode")
