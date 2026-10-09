"""進項發票獨立成表（docs/70 §5；ADR-030；店主 2026-10-08）。

- 新表 purchase_input_invoices：一張發票可涵蓋多批收貨（跨採購單、同供應商），事後可登錄、更正。
- goods_receipts 加 input_invoice_id；原本收貨上的五個發票欄位逐筆搬到新表、連結後移除。

降版：每張發票恰好掛一批時搬回原欄位；有合併（一張掛多批）或沒掛任何收貨的發票就拒絕。

Revision ID: f3b7d9e1a5c2
Revises: e2a6c8f4b1d7
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "f3b7d9e1a5c2"
down_revision = "e2a6c8f4b1d7"
branch_labels = None
depends_on = None

_TABLE = "purchase_input_invoices"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("supplier_name", sa.String(150), nullable=False),
        sa.Column("invoice_number", sa.String(10), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("invoice_total", sa.Numeric(12, 0), nullable=False),
        sa.Column("invoice_net", sa.Numeric(12, 0), nullable=False),
        sa.Column("invoice_tax", sa.Numeric(12, 0), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "invoice_net >= 0 AND invoice_tax >= 0 AND invoice_total > 0"
            " AND invoice_net + invoice_tax = invoice_total",
            name="ck_purchase_input_invoices_amounts",
        ),
        sa.CheckConstraint(
            "invoice_number ~ '^[A-Z]{2}[0-9]{8}$'",
            name="ck_purchase_input_invoices_number_format",
        ),
        sa.UniqueConstraint(
            "store_id",
            "invoice_number",
            "invoice_date",
            name="uq_purchase_input_invoices_store_number_date",
        ),
    )
    op.create_index(f"ix_{_TABLE}_store_id", _TABLE, ["store_id"])
    op.create_index(f"ix_{_TABLE}_supplier_id", _TABLE, ["supplier_id"])
    op.add_column(
        "goods_receipts",
        sa.Column("input_invoice_id", sa.Integer(), sa.ForeignKey(f"{_TABLE}.id"), nullable=True),
    )
    op.create_index("ix_goods_receipts_input_invoice_id", "goods_receipts", ["input_invoice_id"])

    # 舊資料：每批收貨上的發票各自成一張（原本就同店同號同日唯一），再連回去。
    op.execute(
        f"""
        INSERT INTO {_TABLE} (store_id, supplier_id, supplier_name, invoice_number, invoice_date,
            invoice_total, invoice_net, invoice_tax, created_by, created_at, updated_at)
        SELECT gr.store_id, po.supplier_id, po.supplier_name, gr.invoice_number, gr.invoice_date,
            gr.invoice_total, gr.invoice_net, gr.invoice_tax, gr.received_by,
            gr.received_at, gr.received_at
        FROM goods_receipts gr JOIN purchase_orders po ON po.id = gr.purchase_order_id
        WHERE gr.invoice_number IS NOT NULL
        ORDER BY gr.id
        """
    )
    op.execute(
        f"""
        UPDATE goods_receipts gr SET input_invoice_id = inv.id
        FROM {_TABLE} inv
        WHERE inv.store_id = gr.store_id AND inv.invoice_number = gr.invoice_number
          AND inv.invoice_date = gr.invoice_date
        """
    )

    op.drop_index("uq_goods_receipts_store_invoice", table_name="goods_receipts")
    op.drop_constraint("ck_goods_receipts_invoice_consistent", "goods_receipts", type_="check")
    op.drop_constraint("ck_goods_receipts_invoice_number_format", "goods_receipts", type_="check")
    for column in ("invoice_number", "invoice_date", "invoice_total", "invoice_net", "invoice_tax"):
        op.drop_column("goods_receipts", column)


def abort_if_invoices_cannot_fold_back(conn: Connection) -> None:
    """舊結構一批只能放一張發票、發票也不能沒有收貨；做不到就中止降版。"""
    bad = conn.execute(
        sa.text(
            f"""
            SELECT count(*) FROM {_TABLE} inv
            WHERE (SELECT count(*) FROM goods_receipts gr WHERE gr.input_invoice_id = inv.id) <> 1
            """
        )
    ).scalar_one()
    if bad:
        raise RuntimeError(
            f"拒絕降版：有 {bad} 張進項發票涵蓋多批收貨或沒有收貨，舊結構放不下；"
            "請留在新版程式、不要降資料庫（舊程式讀不懂新的發票／更正資料）"
        )


def downgrade() -> None:
    abort_if_invoices_cannot_fold_back(op.get_bind())
    op.add_column("goods_receipts", sa.Column("invoice_number", sa.String(10), nullable=True))
    op.add_column("goods_receipts", sa.Column("invoice_date", sa.Date(), nullable=True))
    for column in ("invoice_total", "invoice_net", "invoice_tax"):
        op.add_column("goods_receipts", sa.Column(column, sa.Numeric(12, 0), nullable=True))
    op.execute(
        f"""
        UPDATE goods_receipts gr SET invoice_number = inv.invoice_number,
            invoice_date = inv.invoice_date, invoice_total = inv.invoice_total,
            invoice_net = inv.invoice_net, invoice_tax = inv.invoice_tax
        FROM {_TABLE} inv WHERE inv.id = gr.input_invoice_id
        """
    )
    op.create_check_constraint(
        "ck_goods_receipts_invoice_consistent",
        "goods_receipts",
        "(invoice_number IS NULL AND invoice_date IS NULL AND invoice_total IS NULL"
        " AND invoice_net IS NULL AND invoice_tax IS NULL)"
        " OR (invoice_number IS NOT NULL AND invoice_date IS NOT NULL"
        " AND invoice_total IS NOT NULL AND invoice_net IS NOT NULL"
        " AND invoice_tax IS NOT NULL AND invoice_net + invoice_tax = invoice_total)",
    )
    op.create_check_constraint(
        "ck_goods_receipts_invoice_number_format",
        "goods_receipts",
        "invoice_number IS NULL OR invoice_number ~ '^[A-Z]{2}[0-9]{8}$'",
    )
    op.create_index(
        "uq_goods_receipts_store_invoice",
        "goods_receipts",
        ["store_id", "invoice_number", "invoice_date"],
        unique=True,
        postgresql_where=sa.text("invoice_number IS NOT NULL"),
    )
    op.drop_index("ix_goods_receipts_input_invoice_id", table_name="goods_receipts")
    op.drop_column("goods_receipts", "input_invoice_id")
    op.drop_index(f"ix_{_TABLE}_supplier_id", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_store_id", table_name=_TABLE)
    op.drop_table(_TABLE)
