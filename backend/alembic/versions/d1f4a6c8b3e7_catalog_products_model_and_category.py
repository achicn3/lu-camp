"""catalog_products 補型號與分類（採購建品比照收購）

Revision ID: d1f4a6c8b3e7
Revises: b7e2c9a4f1d6
Create Date: 2026-09-14 02:10:00.000000

2026-09-14 裁示：採購單建立新品時要能填品牌、型號等，跟收購頁一樣。
`brand_id` 本來就有，缺的是型號與分類——少了它們，同一支營繩在收購來的二手與採購來的
全新之間，庫存頁的篩選與標籤就對不起來（標籤也要印品牌，見同批變更）。

Additive、nullable、無 backfill：既有列一律 NULL＝未填。FK 指向既有的 product_models／
categories，與序號品同一組主檔，不另開一套。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1f4a6c8b3e7"
down_revision: str | Sequence[str] | None = "b7e2c9a4f1d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("catalog_products", sa.Column("product_model_id", sa.Integer(), nullable=True))
    op.add_column("catalog_products", sa.Column("category_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_catalog_products_product_model_id",
        "catalog_products",
        "product_models",
        ["product_model_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_catalog_products_category_id",
        "catalog_products",
        "categories",
        ["category_id"],
        ["id"],
    )
    op.create_index(
        "ix_catalog_products_product_model_id", "catalog_products", ["product_model_id"]
    )
    op.create_index("ix_catalog_products_category_id", "catalog_products", ["category_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_catalog_products_category_id", table_name="catalog_products")
    op.drop_index("ix_catalog_products_product_model_id", table_name="catalog_products")
    op.drop_constraint("fk_catalog_products_category_id", "catalog_products", type_="foreignkey")
    op.drop_constraint(
        "fk_catalog_products_product_model_id", "catalog_products", type_="foreignkey"
    )
    op.drop_column("catalog_products", "category_id")
    op.drop_column("catalog_products", "product_model_id")
