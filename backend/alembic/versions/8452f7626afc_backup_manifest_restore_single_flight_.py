"""backup manifest + restore single-flight index (Codex R4)

Revision ID: 8452f7626afc
Revises: d4b8f1a2c3e5
Create Date: 2026-07-19 22:33:44.610142

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "8452f7626afc"
down_revision: str | Sequence[str] | None = "d4b8f1a2c3e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 備份時擷取的 key-table 筆數快照（還原後比對,擋空/半殘還原被誤判 VERIFIED；Codex R4 #3）。
    op.add_column("backup_runs", sa.Column("manifest", postgresql.JSONB(), nullable=True))
    # 還原單一在跑守衛：同店至多一列 RUNNING（每次還原都 clone 整庫,防併發塞爆；Codex R4 #4）。
    op.create_index(
        "uq_restore_runs_one_running",
        "restore_runs",
        ["store_id"],
        unique=True,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_restore_runs_one_running", table_name="restore_runs")
    op.drop_column("backup_runs", "manifest")
