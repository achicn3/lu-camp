"""發票 OrderId／折讓單號改為建立時隨機產生並持久化（撞號修正，2026-10-01）。

`invoices.platform_order_id`、`invoice_allowances.platform_number`：既有列回填**原本由流水號
推導的值**（`S{store}-{sale}`；折讓沿用舊 `allowance_number()` 的短格式／`LX`+base36 封裝），
已送出或已認領待重送的訊息因此不會在升級後換號——對帳查詢與凍結 payload 照舊對得上。
新列由 ORM 欄位 default 產生帶亂數段的編號。

Revision ID: 3f2adaec554d
Revises: edaa5e5ce783
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "3f2adaec554d"
down_revision = "edaa5e5ce783"
branch_labels = None
depends_on = None

_BASE36_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _base36(value: int) -> str:
    if value == 0:
        return "0"
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        digits.append(_BASE36_ALPHABET[remainder])
    return "".join(reversed(digits))


def legacy_allowance_number(store_id: int, allowance_id: int) -> str:
    """升級前 `amego.allowance_number()` 的原樣複本（凍結於此，不隨 app 程式碼變動）。"""
    readable = f"L{store_id}-{allowance_id}"
    if len(readable) <= 16:
        return readable
    return f"LX{_base36((store_id << 32) | allowance_id)}"


def upgrade() -> None:
    op.add_column("invoices", sa.Column("platform_order_id", sa.String(40), nullable=True))
    op.execute("UPDATE invoices SET platform_order_id = 'S' || store_id || '-' || sale_id")
    op.alter_column("invoices", "platform_order_id", nullable=False)
    op.create_unique_constraint(
        "uq_invoices_store_platform_order_id", "invoices", ["store_id", "platform_order_id"]
    )

    op.add_column("invoice_allowances", sa.Column("platform_number", sa.String(16), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, store_id FROM invoice_allowances")).all()
    for allowance_id, store_id in rows:
        conn.execute(
            sa.text("UPDATE invoice_allowances SET platform_number = :n WHERE id = :id"),
            {"n": legacy_allowance_number(store_id, allowance_id), "id": allowance_id},
        )
    op.alter_column("invoice_allowances", "platform_number", nullable=False)
    op.create_unique_constraint(
        "uq_invoice_allowances_store_platform_number",
        "invoice_allowances",
        ["store_id", "platform_number"],
    )


def abort_if_random_ids_exist(conn: Connection) -> None:
    """舊程式由流水號重新推導編號；已有隨機編號（或換過號）的列降版後會對不上平台。"""
    n_inv = conn.execute(
        sa.text(
            "SELECT count(*) FROM invoices"
            " WHERE platform_order_id <> 'S' || store_id || '-' || sale_id"
        )
    ).scalar_one()
    rows = conn.execute(
        sa.text("SELECT id, store_id, platform_number FROM invoice_allowances")
    ).all()
    n_alw = sum(
        1
        for allowance_id, store_id, number in rows
        if number != legacy_allowance_number(store_id, allowance_id)
    )
    if n_inv or n_alw:
        raise RuntimeError(
            f"拒絕降版：已有 {n_inv} 張發票、{n_alw} 張折讓使用隨機平台編號，"
            "舊程式會用錯的編號向平台查詢與送出"
        )


def downgrade() -> None:
    abort_if_random_ids_exist(op.get_bind())
    op.drop_constraint(
        "uq_invoice_allowances_store_platform_number", "invoice_allowances", type_="unique"
    )
    op.drop_column("invoice_allowances", "platform_number")
    op.drop_constraint("uq_invoices_store_platform_order_id", "invoices", type_="unique")
    op.drop_column("invoices", "platform_order_id")
