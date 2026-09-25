"""排隊收購 I3（docs/42 §6）：付款成立收購、商品待整理。

- serialized_items / bulk_lots 的狀態加 PENDING_LISTING（待整理：已付款、還沒上架；POS 賣不到）。
- intake_batches 加 signature_task_id（整批一份切結）、paid_at、paid_by_user_id。
- intake_batch_acquisitions：付款時依類型成立的收購掛回批次。

降版：若已有待整理商品，舊狀態檢查會擋下——請先整理上架或作廢再降版。
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "a1d6e8f3b5c9"
down_revision = "f7c2a9d4e6b8"
branch_labels = None
depends_on = None

_SERIALIZED_OLD = ("IN_STOCK", "SOLD", "RETURNED_TO_CONSIGNOR", "WRITTEN_OFF")
_BULK_OLD = ("ON_SALE", "SOLD_OUT", "WRITTEN_OFF")


def _status_in(values: tuple[str, ...]) -> str:
    return "status IN (" + ", ".join(f"'{v}'" for v in values) + ")"


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
    op.drop_constraint("serializeditemstatus", "serialized_items", type_="check")
    op.create_check_constraint(
        "serializeditemstatus",
        "serialized_items",
        _status_in(("PENDING_LISTING", *_SERIALIZED_OLD)),
    )
    op.drop_constraint("bulklotstatus", "bulk_lots", type_="check")
    op.create_check_constraint(
        "bulklotstatus", "bulk_lots", _status_in(("PENDING_LISTING", *_BULK_OLD))
    )
    op.add_column(
        "intake_batches",
        sa.Column(
            "signature_task_id", sa.Integer(), sa.ForeignKey("signature_tasks.id"), nullable=True
        ),
    )
    op.add_column("intake_batches", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "intake_batches",
        sa.Column("paid_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_table(
        "intake_batch_acquisitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("intake_batches.id"), nullable=False),
        sa.Column("acquisition_id", sa.Integer(), sa.ForeignKey("acquisitions.id"), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("acquisition_id", name="uq_intake_batch_acquisitions_acquisition"),
    )
    op.create_index(
        "ix_intake_batch_acquisitions_store_id", "intake_batch_acquisitions", ["store_id"]
    )
    op.create_index(
        "ix_intake_batch_acquisitions_batch_id", "intake_batch_acquisitions", ["batch_id"]
    )


def downgrade() -> None:
    op.drop_table("intake_batch_acquisitions")
    op.drop_column("intake_batches", "paid_by_user_id")
    op.drop_column("intake_batches", "paid_at")
    op.drop_column("intake_batches", "signature_task_id")
    op.drop_constraint("bulklotstatus", "bulk_lots", type_="check")
    op.create_check_constraint("bulklotstatus", "bulk_lots", _status_in(_BULK_OLD))
    op.drop_constraint("serializeditemstatus", "serialized_items", type_="check")
    op.create_check_constraint(
        "serializeditemstatus", "serialized_items", _status_in(_SERIALIZED_OLD)
    )
