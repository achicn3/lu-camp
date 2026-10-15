"""Remember pushed photos/fonts per cloud URL so switching clouds re-pushes them."""

import sqlalchemy as sa
from alembic import op

revision = "b5e2c8f4a1d7"
down_revision = "a3d9e1f7c5b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 舊紀錄不知道推到哪個雲端（cloud＝''）：任何雲端都當作還沒推過，
    # 下次發佈重推一次（雲端冪等）。
    op.add_column(
        "online_pushed_media",
        sa.Column("cloud", sa.String(200), nullable=False, server_default=sa.text("''")),
    )
    op.drop_constraint("uq_online_pushed_media", "online_pushed_media", type_="unique")
    op.create_unique_constraint(
        "uq_online_pushed_media", "online_pushed_media", ["store_id", "cloud", "kind", "sha256"]
    )


def downgrade() -> None:
    # 降版：同一張照片在多個雲端的紀錄併成一筆（只為省流量，少記一筆最多重推一次）。
    op.execute(
        "DELETE FROM online_pushed_media a USING online_pushed_media b "
        "WHERE a.store_id = b.store_id AND a.kind = b.kind AND a.sha256 = b.sha256 AND a.id > b.id"
    )
    op.drop_constraint("uq_online_pushed_media", "online_pushed_media", type_="unique")
    op.create_unique_constraint(
        "uq_online_pushed_media", "online_pushed_media", ["store_id", "kind", "sha256"]
    )
    op.drop_column("online_pushed_media", "cloud")
