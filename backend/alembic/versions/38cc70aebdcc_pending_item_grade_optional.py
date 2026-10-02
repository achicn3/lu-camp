"""待整理序號品可以先沒有成色（docs/42 §13；店主 2026-10-02 快速估價改版）。

只有「待整理」（PENDING_LISTING）的序號品可以沒有成色；其他狀態一律要有，資料庫層擋住，
任何寫入路徑都繞不過去。上架（轉在庫）時一定要選成色。

Revision ID: 38cc70aebdcc
Revises: da91ffee580d
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "38cc70aebdcc"
down_revision = "da91ffee580d"
branch_labels = None
depends_on = None

_CHECK = "ck_serialized_items_grade_required"


def upgrade() -> None:
    op.alter_column("serialized_items", "grade", existing_type=sa.String(), nullable=True)
    op.create_check_constraint(
        _CHECK, "serialized_items", "grade IS NOT NULL OR status = 'PENDING_LISTING'"
    )


def abort_if_gradeless_items_exist(conn: Connection) -> None:
    """降版會把成色改回必填；還有沒成色的待整理商品就中止（不替它們亂填成色）。"""
    n = conn.execute(
        sa.text("SELECT count(*) FROM serialized_items WHERE grade IS NULL")
    ).scalar_one()
    if n:
        raise RuntimeError(f"拒絕降版：還有 {n} 件待整理商品沒有成色，請先上架或補成色")


def downgrade() -> None:
    abort_if_gradeless_items_exist(op.get_bind())
    op.drop_constraint(_CHECK, "serialized_items", type_="check")
    op.alter_column("serialized_items", "grade", existing_type=sa.String(), nullable=False)
