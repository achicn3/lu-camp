"""排隊收購估價時選類型：散裝件數＋只賣寄售的切結不用選撥款（docs/42 §13；店主 2026-10-02）。

1. 散裝填「整堆總價」，件數可以不填（沒填＝整堆算 1 件）。新欄 intake_lines.bulk_piece_count
   只給散裝：填了就是付款後散裝批的總件數，整堆總價放在 deal_cost（該列數量固定 1）。
2. 寄售品也要簽切結。只賣寄售時現在不付錢、合計 0，客人不用選現金或購物金：已簽切結
   「一定要有撥款選擇」放寬為「合計是 0 的可以沒有」。合計大於 0 的仍一定要有（收購綁定也照舊
   拒絕沒有撥款選擇的切結）。

Revision ID: 7b2e4f19c0a5
Revises: 38cc70aebdcc
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "7b2e4f19c0a5"
down_revision = "38cc70aebdcc"
branch_labels = None
depends_on = None

_CHECK = "ck_intake_lines_bulk_piece_count"
_PAYOUT = "ck_signature_tasks_signed_affidavit_payout"
_PAYOUT_OLD = (
    "NOT (status IN ('SIGNED','CONSUMED','FAILED') AND kind = 'ACQUISITION_AFFIDAVIT') "
    "OR chosen_payout IS NOT NULL"
)


def upgrade() -> None:
    op.add_column("intake_lines", sa.Column("bulk_piece_count", sa.Integer(), nullable=True))
    op.create_check_constraint(
        _CHECK,
        "intake_lines",
        "bulk_piece_count IS NULL"
        " OR (bulk_piece_count BETWEEN 1 AND 99999 AND acquisition_type = 'BULK_LOT')",
    )
    op.drop_constraint(_PAYOUT, "signature_tasks", type_="check")
    op.create_check_constraint(
        _PAYOUT, "signature_tasks", f"{_PAYOUT_OLD} OR (content->>'total') = '0'"
    )


def abort_if_payoutless_affidavits_exist(conn: Connection) -> None:
    """降版會把撥款選擇改回必填；已有只賣寄售、沒選撥款的已簽切結就中止（不替它們亂填）。"""
    n = conn.execute(
        sa.text(
            "SELECT count(*) FROM signature_tasks WHERE kind = 'ACQUISITION_AFFIDAVIT'"
            " AND status IN ('SIGNED','CONSUMED','FAILED') AND chosen_payout IS NULL"
        )
    ).scalar_one()
    if n:
        raise RuntimeError(f"拒絕降版：已有 {n} 份只賣寄售的切結沒有撥款選擇")


def downgrade() -> None:
    abort_if_payoutless_affidavits_exist(op.get_bind())
    op.drop_constraint(_PAYOUT, "signature_tasks", type_="check")
    op.create_check_constraint(_PAYOUT, "signature_tasks", _PAYOUT_OLD)
    op.drop_constraint(_CHECK, "intake_lines", type_="check")
    op.drop_column("intake_lines", "bulk_piece_count")
