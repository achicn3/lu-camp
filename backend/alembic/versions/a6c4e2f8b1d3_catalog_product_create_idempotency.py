"""一般商品建檔冪等鍵與原始內容指紋

Revision ID: a6c4e2f8b1d3
Revises: 8452f7626afc
Create Date: 2026-07-21

SKU 留白由系統產生時，網路回應遺失後重送不得建立第二筆商品；以同店冪等鍵唯一約束與
原始請求指紋支援安全回放，同 key 不同內容則拒絕。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6c4e2f8b1d3"
down_revision: str | None = "8452f7626afc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalog_products", sa.Column("create_idempotency_key", sa.String(80), nullable=True)
    )
    op.add_column("catalog_products", sa.Column("create_fingerprint", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "uq_catalog_products_store_create_idempotency",
        "catalog_products",
        ["store_id", "create_idempotency_key"],
    )
    op.create_check_constraint(
        "ck_catalog_products_create_idempotency_pair",
        "catalog_products",
        "(create_idempotency_key IS NULL) = (create_fingerprint IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_catalog_products_create_idempotency_pair", "catalog_products", type_="check"
    )
    op.drop_constraint(
        "uq_catalog_products_store_create_idempotency", "catalog_products", type_="unique"
    )
    op.drop_column("catalog_products", "create_fingerprint")
    op.drop_column("catalog_products", "create_idempotency_key")
