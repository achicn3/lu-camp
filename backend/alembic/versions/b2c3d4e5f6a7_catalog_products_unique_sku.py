"""catalog_products 同店 SKU 唯一約束（與 brands/suppliers 同慣例）

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-23

防止同店重複 SKU（app 層 check-then-insert 競態的 DB 後盾）。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_catalog_products_store_sku", "catalog_products", ["store_id", "sku"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_catalog_products_store_sku", "catalog_products", type_="unique")
