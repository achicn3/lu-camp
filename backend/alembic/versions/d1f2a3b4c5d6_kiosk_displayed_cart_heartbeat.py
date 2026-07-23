"""保存客顯實際顯示的購物車版本，供 POS 連線狀態判讀。

Revision ID: d1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-07-24 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "kiosk_devices",
        sa.Column("displayed_cart_session_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "kiosk_devices",
        sa.Column(
            "displayed_revision",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_kiosk_devices_displayed_cart_session_id",
        "kiosk_devices",
        ["displayed_cart_session_id"],
    )
    op.create_foreign_key(
        "fk_kiosk_devices_displayed_cart_store",
        "kiosk_devices",
        "cart_sessions",
        ["displayed_cart_session_id", "store_id"],
        ["id", "store_id"],
        use_alter=True,
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_kiosk_devices_displayed_cart_store",
        "kiosk_devices",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_kiosk_devices_displayed_cart_session_id",
        table_name="kiosk_devices",
    )
    op.drop_column("kiosk_devices", "displayed_revision")
    op.drop_column("kiosk_devices", "displayed_cart_session_id")
