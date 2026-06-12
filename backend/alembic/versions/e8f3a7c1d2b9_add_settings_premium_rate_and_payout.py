"""add settings premium_rate and acquisition payout fields

Revision ID: e8f3a7c1d2b9
Revises: c5d1e8a2b7f4
Create Date: 2026-06-12 16:00:00.000000

SC-2（docs/16 §1.7／§3.1）：收購撥款 CASH | STORE_CREDIT | SPLIT。
settings.premium_rate 為 SC-5 的最小前移（入帳需當下溢價率）。
既有收購資料一律視為付現（payout_method='CASH'）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8f3a7c1d2b9"
down_revision: str | Sequence[str] | None = "c5d1e8a2b7f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "settings",
        sa.Column("premium_rate", sa.Numeric(5, 4), server_default=sa.text("0.10"), nullable=False),
    )
    op.add_column(
        "acquisitions",
        sa.Column(
            "payout_method",
            sa.Enum(
                "CASH",
                "STORE_CREDIT",
                "SPLIT",
                name="payoutmethod",
                native_enum=False,
                length=20,
                create_constraint=True,
            ),
            server_default=sa.text("'CASH'"),
            nullable=False,
        ),
    )
    op.add_column(
        "acquisitions",
        sa.Column("payout_cash_amount", sa.Numeric(12, 0), nullable=True),
    )
    op.add_column(
        "acquisitions",
        sa.Column("payout_credit_cash_equivalent", sa.Numeric(12, 0), nullable=True),
    )
    op.add_column("acquisitions", sa.Column("idempotency_key", sa.String(80), nullable=True))
    op.add_column(
        "acquisitions", sa.Column("idempotency_fingerprint", sa.String(64), nullable=True)
    )
    op.create_unique_constraint(
        "uq_acquisitions_store_idem_key", "acquisitions", ["store_id", "idempotency_key"]
    )
    op.create_check_constraint(
        "ck_acquisitions_idem_key_nonempty",
        "acquisitions",
        "idempotency_key IS NULL OR length(idempotency_key) > 0",
    )
    op.create_check_constraint(
        "ck_acquisitions_payout_cash_nonneg",
        "acquisitions",
        "payout_cash_amount IS NULL OR payout_cash_amount >= 0",
    )
    op.create_check_constraint(
        "ck_acquisitions_payout_credit_nonneg",
        "acquisitions",
        "payout_credit_cash_equivalent IS NULL OR payout_credit_cash_equivalent >= 0",
    )
    # 回填必須先於形狀 CHECK（Codex 第十三輪 high：CHECK 建立時即驗既有列，
    # 舊付現單尚未補拆分欄會讓 migration 直接失敗、正式庫無法升級）。
    op.execute(
        "UPDATE acquisitions SET payout_cash_amount = total_cash_paid,"
        " payout_credit_cash_equivalent = 0 WHERE total_cash_paid IS NOT NULL"
    )
    op.create_check_constraint(
        "ck_acquisitions_consignment_no_payout",
        "acquisitions",
        "type <> 'CONSIGNMENT' OR (payout_method = 'CASH'"
        " AND payout_cash_amount IS NULL AND payout_credit_cash_equivalent IS NULL"
        " AND total_cash_paid IS NULL)",
    )
    op.create_check_constraint(
        "ck_acquisitions_cash_shape",
        "acquisitions",
        "payout_method <> 'CASH' OR type = 'CONSIGNMENT'"
        " OR (payout_cash_amount IS NOT NULL AND total_cash_paid IS NOT NULL"
        " AND payout_cash_amount = total_cash_paid"
        " AND COALESCE(payout_credit_cash_equivalent, 0) = 0)",
    )
    op.create_check_constraint(
        "ck_acquisitions_store_credit_shape",
        "acquisitions",
        "payout_method <> 'STORE_CREDIT' OR (payout_credit_cash_equivalent IS NOT NULL"
        " AND payout_credit_cash_equivalent > 0"
        " AND COALESCE(payout_cash_amount, 0) = 0 AND COALESCE(total_cash_paid, 0) = 0)",
    )
    op.create_check_constraint(
        "ck_acquisitions_split_shape",
        "acquisitions",
        "payout_method <> 'SPLIT' OR (payout_cash_amount IS NOT NULL"
        " AND payout_credit_cash_equivalent IS NOT NULL AND total_cash_paid IS NOT NULL"
        " AND payout_cash_amount > 0 AND payout_credit_cash_equivalent > 0"
        " AND total_cash_paid = payout_cash_amount)",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_acquisitions_split_shape", "acquisitions", type_="check")
    op.drop_constraint("ck_acquisitions_store_credit_shape", "acquisitions", type_="check")
    op.drop_constraint("ck_acquisitions_cash_shape", "acquisitions", type_="check")
    op.drop_constraint("ck_acquisitions_consignment_no_payout", "acquisitions", type_="check")
    op.drop_constraint("ck_acquisitions_payout_credit_nonneg", "acquisitions", type_="check")
    op.drop_constraint("ck_acquisitions_payout_cash_nonneg", "acquisitions", type_="check")
    op.drop_constraint("ck_acquisitions_idem_key_nonempty", "acquisitions", type_="check")
    op.drop_constraint("uq_acquisitions_store_idem_key", "acquisitions", type_="unique")
    op.drop_column("acquisitions", "idempotency_fingerprint")
    op.drop_column("acquisitions", "idempotency_key")
    op.drop_column("acquisitions", "payout_credit_cash_equivalent")
    op.drop_column("acquisitions", "payout_cash_amount")
    op.drop_column("acquisitions", "payout_method")
    op.drop_column("settings", "premium_rate")
