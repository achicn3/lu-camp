"""門市活動 v2 P1c（docs/40）：sale_campaign_overrides——店員在某筆按「這筆不套用」的紀錄。"""

import sqlalchemy as sa
from alembic import op

revision = "b8e4f2a6d1c3"
down_revision = "a3d7e1f9c2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sale_campaign_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id"), nullable=False),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("sale_id", "campaign_id", name="uq_sale_campaign_overrides_entry"),
    )
    op.create_index("ix_sale_campaign_overrides_store_id", "sale_campaign_overrides", ["store_id"])
    op.create_index("ix_sale_campaign_overrides_sale_id", "sale_campaign_overrides", ["sale_id"])


def downgrade() -> None:
    op.drop_table("sale_campaign_overrides")
