"""product_models unique by (store, brand, name)

Revision ID: c1a2b3d4e5f6
Revises: b3d9f2a1c7e5
Create Date: 2026-06-16 10:00:00.000000

F6 A1：型號唯一鍵由 (store, name) 改為 (store, brand, name)，使不同品牌可有同名型號
（收購頁品牌範圍 autocomplete 需要）。既有資料受舊約束保證無 (store,name) 重複 → 新約束必滿足。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c1a2b3d4e5f6"
down_revision: str | Sequence[str] | None = "b3d9f2a1c7e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("uq_product_models_store_name", "product_models", type_="unique")
    op.create_unique_constraint(
        "uq_product_models_store_brand_name",
        "product_models",
        ["store_id", "brand_id", "name"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_product_models_store_brand_name", "product_models", type_="unique")
    op.create_unique_constraint(
        "uq_product_models_store_name", "product_models", ["store_id", "name"]
    )
