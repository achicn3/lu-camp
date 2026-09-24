"""門市活動 v2 P2（docs/40 §2）：活動類型——打折／指定特價／每件折金額。

- campaigns.kind（預設 PERCENT_OFF，既有活動都是打折）、fixed_price、amount_off（含稅整數元）。
- discount_pct 改可空：只有打折需要。原本的 1–99 檢查改為「類型與數值一致」的檢查：
  打折只填 discount_pct，特價只填 fixed_price（> 0），折金額只填 amount_off（> 0）。

降版：若已有特價或折金額活動，discount_pct 無法改回必填——那是刻意的，舊程式看不懂這兩種活動，
請先作廢／刪除它們再降版。
"""

import sqlalchemy as sa
from alembic import op

revision = "c9f1a3e5b7d2"
down_revision = "b8e4f2a6d1c3"
branch_labels = None
depends_on = None

_KIND_VALUE_CHECK = (
    "(kind = 'PERCENT_OFF' AND discount_pct BETWEEN 1 AND 99"
    " AND fixed_price IS NULL AND amount_off IS NULL)"
    " OR (kind = 'FIXED_PRICE' AND fixed_price > 0"
    " AND discount_pct IS NULL AND amount_off IS NULL)"
    " OR (kind = 'AMOUNT_OFF' AND amount_off > 0"
    " AND discount_pct IS NULL AND fixed_price IS NULL)"
)


def upgrade() -> None:
    op.add_column(
        "campaigns",
        sa.Column(
            "kind",
            sa.Enum(
                "PERCENT_OFF",
                "FIXED_PRICE",
                "AMOUNT_OFF",
                name="campaignkind",
                native_enum=False,
                length=30,
                create_constraint=True,
            ),
            server_default="PERCENT_OFF",
            nullable=False,
        ),
    )
    op.add_column("campaigns", sa.Column("fixed_price", sa.Numeric(12, 0), nullable=True))
    op.add_column("campaigns", sa.Column("amount_off", sa.Numeric(12, 0), nullable=True))
    op.drop_constraint("ck_campaigns_discount_pct", "campaigns", type_="check")
    op.alter_column("campaigns", "discount_pct", existing_type=sa.Integer(), nullable=True)
    op.create_check_constraint("ck_campaigns_kind_value", "campaigns", _KIND_VALUE_CHECK)


def downgrade() -> None:
    op.drop_constraint("ck_campaigns_kind_value", "campaigns", type_="check")
    op.alter_column("campaigns", "discount_pct", existing_type=sa.Integer(), nullable=False)
    op.create_check_constraint(
        "ck_campaigns_discount_pct", "campaigns", "discount_pct >= 1 AND discount_pct <= 99"
    )
    op.drop_column("campaigns", "amount_off")
    op.drop_column("campaigns", "fixed_price")
    op.drop_column("campaigns", "kind")
