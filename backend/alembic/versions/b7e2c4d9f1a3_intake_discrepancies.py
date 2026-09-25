"""排隊收購：上架時的差異紀錄（docs/42 §8）。

intake_discrepancies：少件或壞到不能賣的件數與原因；那幾件另以庫存報廢出庫，成交件數與成本不改。
"""

import sqlalchemy as sa
from alembic import op

revision = "b7e2c4d9f1a3"
down_revision = "a1d6e8f3b5c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "intake_discrepancies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("intake_batches.id"), nullable=False),
        sa.Column("serialized_item_id", sa.Integer(), sa.ForeignKey("serialized_items.id")),
        sa.Column("bulk_lot_id", sa.Integer(), sa.ForeignKey("bulk_lots.id")),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("qty >= 1", name="ck_intake_discrepancies_qty_pos"),
        sa.CheckConstraint(
            "(serialized_item_id IS NULL) <> (bulk_lot_id IS NULL)",
            name="ck_intake_discrepancies_one_item",
        ),
        sa.CheckConstraint(
            "serialized_item_id IS NULL OR qty = 1", name="ck_intake_discrepancies_serialized_one"
        ),
    )
    op.create_index("ix_intake_discrepancies_store_id", "intake_discrepancies", ["store_id"])
    op.create_index("ix_intake_discrepancies_batch_id", "intake_discrepancies", ["batch_id"])


def downgrade() -> None:
    op.drop_table("intake_discrepancies")
