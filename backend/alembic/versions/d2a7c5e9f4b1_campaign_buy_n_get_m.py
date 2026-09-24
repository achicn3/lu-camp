"""門市活動 v2 P3（docs/40 §2）：買 N 送 M。

- campaigns.kind 加 BUY_N_GET_M；新欄 buy_qty（N）、free_qty（M），都是 1–99 的整數。
- 「類型與數值一致」的檢查加一種：買 N 送 M 只填 buy_qty、free_qty，而且不可開寄售（裁示 7）。

降版：若已有買 N 送 M 活動，舊檢查會擋下——舊程式看不懂這種活動，請先作廢／刪除再降版。
"""

import sqlalchemy as sa
from alembic import op

revision = "d2a7c5e9f4b1"
down_revision = "c9f1a3e5b7d2"
branch_labels = None
depends_on = None

_OLD_KINDS = ("PERCENT_OFF", "FIXED_PRICE", "AMOUNT_OFF")
_NEW_KINDS = (*_OLD_KINDS, "BUY_N_GET_M")

_NO_BNGM = " AND buy_qty IS NULL AND free_qty IS NULL"
_OLD_KIND_VALUE_CHECK = (
    "(kind = 'PERCENT_OFF' AND discount_pct BETWEEN 1 AND 99"
    " AND fixed_price IS NULL AND amount_off IS NULL)"
    " OR (kind = 'FIXED_PRICE' AND fixed_price > 0"
    " AND discount_pct IS NULL AND amount_off IS NULL)"
    " OR (kind = 'AMOUNT_OFF' AND amount_off > 0"
    " AND discount_pct IS NULL AND fixed_price IS NULL)"
)
_NEW_KIND_VALUE_CHECK = (
    "(kind = 'PERCENT_OFF' AND discount_pct BETWEEN 1 AND 99"
    f" AND fixed_price IS NULL AND amount_off IS NULL{_NO_BNGM})"
    " OR (kind = 'FIXED_PRICE' AND fixed_price > 0"
    f" AND discount_pct IS NULL AND amount_off IS NULL{_NO_BNGM})"
    " OR (kind = 'AMOUNT_OFF' AND amount_off > 0"
    f" AND discount_pct IS NULL AND fixed_price IS NULL{_NO_BNGM})"
    " OR (kind = 'BUY_N_GET_M' AND buy_qty BETWEEN 1 AND 99 AND free_qty BETWEEN 1 AND 99"
    " AND discount_pct IS NULL AND fixed_price IS NULL AND amount_off IS NULL"
    " AND NOT applies_consignment)"
)


def _kind_in(kinds: tuple[str, ...]) -> str:
    return "kind IN (" + ", ".join(f"'{k}'" for k in kinds) + ")"


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("buy_qty", sa.Integer(), nullable=True))
    op.add_column("campaigns", sa.Column("free_qty", sa.Integer(), nullable=True))
    op.drop_constraint("campaignkind", "campaigns", type_="check")
    op.create_check_constraint("campaignkind", "campaigns", _kind_in(_NEW_KINDS))
    op.drop_constraint("ck_campaigns_kind_value", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_kind_value", "campaigns", _NEW_KIND_VALUE_CHECK)


def downgrade() -> None:
    op.drop_constraint("ck_campaigns_kind_value", "campaigns", type_="check")
    op.create_check_constraint("ck_campaigns_kind_value", "campaigns", _OLD_KIND_VALUE_CHECK)
    op.drop_constraint("campaignkind", "campaigns", type_="check")
    op.create_check_constraint("campaignkind", "campaigns", _kind_in(_OLD_KINDS))
    op.drop_column("campaigns", "free_qty")
    op.drop_column("campaigns", "buy_qty")
