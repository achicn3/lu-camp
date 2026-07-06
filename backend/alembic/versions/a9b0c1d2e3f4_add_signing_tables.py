"""add signing tables（K2 手持簽署，docs/23）＋ users.role 擴充 KIOSK

- users.role CHECK（native_enum=False 名 'userrole'）加入 'KIOSK'：手持簽署裝置專用角色（D4），
  中央預設拒絕一般店務端點、僅能使用 /kiosk。
- agreement_versions：切結書/條款版本（不可變，改版＝新列；D8 v1 先行）。
- signature_tasks：簽署任務——content JSONB 顯示快照、簽名 PNG bytes、AFFIDAVIT 必綁條款版本、
  chosen_payout 限二選一（D7，CHECK 擋 SPLIT）、SIGNED 必有簽名與時間戳。

Revision ID: a9b0c1d2e3f4
Revises: e5f6a7b8c9d0
Create Date: 2026-07-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "a9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=30, create_constraint=True)


_USER_ROLE_OLD = "'MANAGER', 'CLERK'"
_USER_ROLE_NEW = "'MANAGER', 'CLERK', 'KIOSK'"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint("userrole", "users", type_="check")
    op.create_check_constraint("userrole", "users", f"role IN ({_USER_ROLE_NEW})")

    op.create_table(
        "agreement_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("version", name="uq_agreement_versions_version"),
    )

    op.create_table(
        "signature_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column(
            "kind",
            _enum(
                "ACQUISITION_AFFIDAVIT",
                "STORE_CREDIT_USE",
                "TRANSACTION_ACK",
                name="signaturetaskkind",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum("PENDING", "SIGNED", "CANCELLED", name="signaturetaskstatus"),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column(
            "agreement_version_id",
            sa.Integer(),
            sa.ForeignKey("agreement_versions.id"),
            nullable=True,
        ),
        sa.Column(
            "chosen_payout",
            _enum("CASH", "STORE_CREDIT", "SPLIT", name="payoutmethod"),
            nullable=True,
        ),
        sa.Column("signature_image", sa.LargeBinary(), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ref_type", sa.String(length=30), nullable=True),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["contact_id", "store_id"],
            ["contacts.id", "contacts.store_id"],
            name="fk_signature_tasks_contact_store",
        ),
        sa.CheckConstraint(
            "status <> 'SIGNED' OR (signature_image IS NOT NULL AND signed_at IS NOT NULL)",
            name="ck_signature_tasks_signed_evidence",
        ),
        sa.CheckConstraint(
            "kind <> 'ACQUISITION_AFFIDAVIT' OR agreement_version_id IS NOT NULL",
            name="ck_signature_tasks_affidavit_agreement",
        ),
        sa.CheckConstraint(
            "chosen_payout IS NULL OR chosen_payout <> 'SPLIT'",
            name="ck_signature_tasks_payout_binary",
        ),
    )
    op.create_index("ix_signature_tasks_store_id", "signature_tasks", ["store_id"])
    op.create_index("ix_signature_tasks_contact_id", "signature_tasks", ["contact_id"])
    op.create_index("ix_signature_tasks_store_status", "signature_tasks", ["store_id", "status"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_signature_tasks_store_status", table_name="signature_tasks")
    op.drop_index("ix_signature_tasks_contact_id", table_name="signature_tasks")
    op.drop_index("ix_signature_tasks_store_id", table_name="signature_tasks")
    op.drop_table("signature_tasks")
    op.drop_table("agreement_versions")
    op.drop_constraint("userrole", "users", type_="check")
    op.create_check_constraint("userrole", "users", f"role IN ({_USER_ROLE_OLD})")
