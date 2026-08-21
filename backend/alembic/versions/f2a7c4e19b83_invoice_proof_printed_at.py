"""invoices 加 proof_printed_at：記錄證明聯實際印出的時間

決定下一次列印該用正本還是補印（電子發票實施作業要點 §26：證明聯以列印一次為限；
補印須加註「補印」二字且併同原聯兌獎）。

既有資料一律填 NULL 而**不是**回填成「已印過」：舊資料沒有這個事實，
猜成已印過會讓正常的第一次列印變成補印，反而印出兌不了獎的紙；猜成未印過
則最多是多印一張正本，由店員判斷。兩害相權取後者。

Revision ID: f2a7c4e19b83
Revises: e1f3a5c7b9d2
"""

import sqlalchemy as sa
from alembic import op

revision = "f2a7c4e19b83"
down_revision = "e1f3a5c7b9d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("proof_printed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoices", "proof_printed_at")
