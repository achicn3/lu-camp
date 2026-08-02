"""退貨退款金額可為 0（純贈品退貨）

純贈品的退貨要回補庫存、寫 GIFT_RETURN 異動，但沒有錢可退——原本的
`refund_amount > 0` 會直接把它擋在門外，且錯誤訊息誤導成「累計退款金額超過原付款渠道金額」。
負數仍然不合法。

`return_tenders.amount > 0` **不動**：零元退貨不產生任何退款渠道明細，
deferred 對平守衛看的是加總（0 == 0 仍成立）。

Revision ID: c7e9a1b3d5f2
Revises: b5d7f9a1c3e2
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c7e9a1b3d5f2"
down_revision: str | None = "b5d7f9a1c3e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_returns_refund_amount_positive", "returns", type_="check")
    op.create_check_constraint(
        "ck_returns_refund_amount_positive", "returns", "refund_amount >= 0"
    )


def downgrade() -> None:
    # 回滾前必須沒有零元退貨，否則約束建不起來（這正是我們要知道的事，不靜默略過）。
    op.drop_constraint("ck_returns_refund_amount_positive", "returns", type_="check")
    op.create_check_constraint(
        "ck_returns_refund_amount_positive", "returns", "refund_amount > 0"
    )
