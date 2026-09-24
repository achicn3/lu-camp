"""收購佇列 I1（docs/42）：批次與估價列。

- intake_batches：一位客人一批；同店同一台北營業日的流水號（A001）、
  點清件數、狀態、取消紀錄。
- intake_lines：每列估價（簡稱、數量、類型、原價、折數、預計售價、
  建議／成交收購價、抽成、選填的成色分類品牌型號、備註）
  ＋叫號處置（接受件數、是否已交還客人）。
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "f7c2a9d4e6b8"
down_revision = "e5b3d8a1c7f2"
branch_labels = None
depends_on = None

_BATCH_STATUSES = (
    "PENDING_ESTIMATE",
    "ESTIMATING",
    "AWAITING_CONFIRM",
    "SIGNED",
    "PAID",
    "PARTIALLY_LISTED",
    "LISTED",
    "CANCELLED",
)
_DISPOSITIONS = ("PENDING", "ACCEPTED", "CUSTOMER_KEPT", "STORE_DECLINED")
_ACQ_TYPES = ("BUYOUT", "CONSIGNMENT", "BULK_LOT")
_GRADES = ("N", "S", "A", "B", "C", "D", "E")


def _enum(values: tuple[str, ...], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=30, create_constraint=True)


def _timestamps() -> list[sa.Column[datetime]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "intake_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("ticket_date", sa.Date(), nullable=False),
        sa.Column("ticket_no", sa.Integer(), nullable=False),
        sa.Column("declared_item_count", sa.Integer(), nullable=False),
        sa.Column("status", _enum(_BATCH_STATUSES, "intakebatchstatus"), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("cancel_reason", sa.String(200), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("declared_item_count >= 1", name="ck_intake_batches_declared_pos"),
        sa.CheckConstraint(
            "(cancelled_at IS NULL AND cancelled_by_user_id IS NULL AND cancel_reason IS NULL)"
            " OR (cancelled_at IS NOT NULL AND cancelled_by_user_id IS NOT NULL"
            " AND cancel_reason IS NOT NULL)",
            name="ck_intake_batches_cancel_shape",
        ),
    )
    op.create_index("ix_intake_batches_store_id", "intake_batches", ["store_id"])
    op.create_index("ix_intake_batches_contact_id", "intake_batches", ["contact_id"])
    op.create_index(
        "uq_intake_batches_store_date_no",
        "intake_batches",
        ["store_id", "ticket_date", "ticket_no"],
        unique=True,
    )
    op.create_index("ix_intake_batches_store_status", "intake_batches", ["store_id", "status"])

    op.create_table(
        "intake_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("intake_batches.id"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("short_name", sa.String(100), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("acquisition_type", _enum(_ACQ_TYPES, "acquisitiontype"), nullable=False),
        sa.Column("reference_price", sa.Numeric(12, 0), nullable=True),
        sa.Column("discount_pct", sa.Integer(), nullable=True),
        sa.Column("expected_listed_price", sa.Numeric(12, 0), nullable=True),
        sa.Column("suggested_cost", sa.Numeric(12, 0), nullable=True),
        sa.Column("deal_cost", sa.Numeric(12, 0), nullable=True),
        sa.Column("commission_pct", sa.Integer(), nullable=True),
        sa.Column("grade", _enum(_GRADES, "grade"), nullable=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), nullable=True),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("brands.id"), nullable=True),
        sa.Column(
            "product_model_id", sa.Integer(), sa.ForeignKey("product_models.id"), nullable=True
        ),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column(
            "disposition",
            _enum(_DISPOSITIONS, "intakedisposition"),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("accepted_qty", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "returned_to_customer", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        *_timestamps(),
        sa.UniqueConstraint("batch_id", "line_no", name="uq_intake_lines_batch_no"),
        sa.CheckConstraint("qty >= 1", name="ck_intake_lines_qty_pos"),
        sa.CheckConstraint(
            "accepted_qty >= 0 AND accepted_qty <= qty", name="ck_intake_lines_accepted_range"
        ),
        sa.CheckConstraint(
            "discount_pct IS NULL OR discount_pct BETWEEN 1 AND 100",
            name="ck_intake_lines_discount_range",
        ),
        sa.CheckConstraint(
            "commission_pct IS NULL OR commission_pct BETWEEN 0 AND 100",
            name="ck_intake_lines_commission_range",
        ),
        sa.CheckConstraint(
            "(reference_price IS NULL OR reference_price >= 0)"
            " AND (expected_listed_price IS NULL OR expected_listed_price >= 0)"
            " AND (suggested_cost IS NULL OR suggested_cost >= 0)"
            " AND (deal_cost IS NULL OR deal_cost >= 0)",
            name="ck_intake_lines_amounts_nonneg",
        ),
    )
    op.create_index("ix_intake_lines_store_id", "intake_lines", ["store_id"])
    op.create_index("ix_intake_lines_batch_id", "intake_lines", ["batch_id"])


def downgrade() -> None:
    op.drop_table("intake_lines")
    op.drop_table("intake_batches")
