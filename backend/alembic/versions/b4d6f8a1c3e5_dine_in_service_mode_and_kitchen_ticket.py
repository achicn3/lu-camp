"""餐飲內用桌號與出餐單（docs/35）

sales 加 service_mode（DINE_IN/TAKEOUT，可空）與 table_no（字串快照，可空），並以
CHECK 守住兩者自洽；settings 加 dine_in_tables（JSONB 桌號清單）與 print_kitchen_ticket。

Revision ID: b4d6f8a1c3e5
Revises: d8f0b2c4e6a1
Create Date: 2026-08-17 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b4d6f8a1c3e5"
down_revision: str | Sequence[str] | None = "d8f0b2c4e6a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 三種合法組合逐一明寫（與 models.Sale.__table_args__ **同名同條件**）。
# 每個比較都必須 NULL-safe：Postgres 的 CHECK 只在結果為 false 時拒絕，NULL 一律放行。
# 寫成 `service_mode = 'DINE_IN'` 時，service_mode 為 NULL 會讓整條變 NULL，
# (NULL, 'A1') 這種髒列照樣進得來（回歸測試已涵蓋）。`IS NOT DISTINCT FROM` 永遠回 true/false。
_SERVICE_MODE_CK = "servicemode"
# 與 `app.shared.enums.ServiceMode` 同步（由 test_enum_check_constraint_sync 守衛：
# 只加 enum 值卻忘了改 migration，測試庫是 create_all 建的會全綠、真 DB 卻寫不進去）。
_SERVICE_MODES = ("DINE_IN", "TAKEOUT")

_SERVICE_MODE_CHECK = (
    "(service_mode IS NOT DISTINCT FROM 'DINE_IN' AND table_no IS NOT NULL)"
    " OR (service_mode IS NOT DISTINCT FROM 'TAKEOUT' AND table_no IS NULL)"
    " OR (service_mode IS NULL AND table_no IS NULL)"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("sales", sa.Column("service_mode", sa.String(length=30), nullable=True))
    op.add_column("sales", sa.Column("table_no", sa.String(length=20), nullable=True))
    # 列舉存 VARCHAR + CHECK（native_enum=False），約束名沿用 SQLAlchemy 的 enum 名慣例。
    allowed = ", ".join(f"'{v}'" for v in _SERVICE_MODES)
    op.create_check_constraint(
        _SERVICE_MODE_CK, "sales", sa.text(f"service_mode IN ({allowed})")
    )
    op.create_check_constraint("ck_sales_service_mode_table_no", "sales", _SERVICE_MODE_CHECK)

    op.add_column(
        "settings",
        sa.Column(
            "dine_in_tables",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "settings",
        sa.Column(
            "print_kitchen_ticket",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("settings", "print_kitchen_ticket")
    op.drop_column("settings", "dine_in_tables")
    op.drop_constraint("ck_sales_service_mode_table_no", "sales", type_="check")
    op.drop_constraint(_SERVICE_MODE_CK, "sales", type_="check")
    op.drop_column("sales", "table_no")
    op.drop_column("sales", "service_mode")
