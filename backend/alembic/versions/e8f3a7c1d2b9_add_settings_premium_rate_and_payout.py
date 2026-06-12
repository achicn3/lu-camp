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
    # legacy 回填（Codex medium）：既有付現單以 CASH 語意補齊拆分欄，
    # 避免新欄位讀起來像「資料缺漏」而非「全額付現」。
    op.execute(
        "UPDATE acquisitions SET payout_cash_amount = total_cash_paid,"
        " payout_credit_cash_equivalent = 0 WHERE total_cash_paid IS NOT NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_acquisitions_idem_key_nonempty", "acquisitions", type_="check")
    op.drop_constraint("uq_acquisitions_store_idem_key", "acquisitions", type_="unique")
    op.drop_column("acquisitions", "idempotency_fingerprint")
    op.drop_column("acquisitions", "idempotency_key")
    op.drop_column("acquisitions", "payout_credit_cash_equivalent")
    op.drop_column("acquisitions", "payout_cash_amount")
    op.drop_column("acquisitions", "payout_method")
    op.drop_column("settings", "premium_rate")
