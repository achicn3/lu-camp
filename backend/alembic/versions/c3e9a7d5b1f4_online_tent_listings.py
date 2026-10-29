"""寄售帳篷上官網（docs/97、ADR-033）。

- online_tent_listings：寄售品的官網資料（上架、保留、顏色、露數、缺件、瑕疵、配件、影片）。
- online_tent_listing_photos：帳篷照片（沿用 menu_photos，每頂最多 8 張）。
- online_tent_publications：清單推到雲端的結果（每店每個雲端一列）。

降版：已有官網資料時拒絕（店員填的資料會整批消失）。

Revision ID: c3e9a7d5b1f4
Revises: f8c4a2e6d0b9
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c3e9a7d5b1f4"
down_revision = "f8c4a2e6d0b9"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def _json_list(name: str) -> sa.Column[Any]:
    return sa.Column(name, JSONB(), nullable=False, server_default=sa.text("'[]'"))


def upgrade() -> None:
    op.create_table(
        "online_tent_listings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "serialized_item_id",
            sa.Integer(),
            sa.ForeignKey("serialized_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reserved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("color", sa.String(30), nullable=True),
        sa.Column("nights", sa.Integer(), nullable=True),
        _json_list("missing"),
        _json_list("defects"),
        _json_list("extras"),
        sa.Column("video_url", sa.String(300), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("store_id", "serialized_item_id", name="uq_online_tent_listings_item"),
        sa.CheckConstraint("nights IS NULL OR nights >= 0", name="ck_online_tent_listings_nights"),
    )
    op.create_index("ix_online_tent_listings_store_id", "online_tent_listings", ["store_id"])
    op.create_index(
        "ix_online_tent_listings_serialized_item_id",
        "online_tent_listings",
        ["serialized_item_id"],
    )

    op.create_table(
        "online_tent_listing_photos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "listing_id",
            sa.Integer(),
            sa.ForeignKey("online_tent_listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("photo_sha256", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "listing_id", "position", name="uq_online_tent_listing_photos_position"
        ),
        sa.UniqueConstraint("listing_id", "photo_sha256", name="uq_online_tent_listing_photos_sha"),
        sa.CheckConstraint(
            "position BETWEEN 0 AND 7", name="ck_online_tent_listing_photos_position"
        ),
        sa.ForeignKeyConstraint(
            ["store_id", "photo_sha256"],
            ["menu_photos.store_id", "menu_photos.sha256"],
            name="fk_online_tent_listing_photos_photo",
        ),
    )
    op.create_index(
        "ix_online_tent_listing_photos_store_id", "online_tent_listing_photos", ["store_id"]
    )
    op.create_index(
        "ix_online_tent_listing_photos_listing_id", "online_tent_listing_photos", ["listing_id"]
    )

    op.create_table(
        "online_tent_publications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("cloud", sa.String(200), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("store_id", "cloud", name="uq_online_tent_publications_cloud"),
    )
    op.create_index(
        "ix_online_tent_publications_store_id", "online_tent_publications", ["store_id"]
    )


def downgrade() -> None:
    has_data = op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM online_tent_listings)"))
    if has_data.scalar():
        raise RuntimeError(
            "已有寄售帳篷的官網資料，降版會整批刪除；"
            "請先確認不再需要後手動清空 online_tent_listings"
        )
    op.drop_table("online_tent_publications")
    op.drop_table("online_tent_listing_photos")
    op.drop_table("online_tent_listings")
