"""簽署任務加同意方式：純餐點退款可在顧客螢幕點選同意（docs/47 E3）。

既有任務一律為手寫簽名（SIGNATURE）；點選同意只限退貨的發票處置同意。

Revision ID: 3c59ce725956
Revises: b86c4571decf
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "3c59ce725956"
down_revision = "b86c4571decf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "signature_tasks",
        sa.Column(
            "consent_mode",
            sa.String(30),
            server_default=sa.text("'SIGNATURE'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_signature_tasks_consent_mode", "signature_tasks", "consent_mode IN ('SIGNATURE','TAP')"
    )
    op.create_check_constraint(
        "ck_signature_tasks_tap_only_return_consent",
        "signature_tasks",
        "consent_mode = 'SIGNATURE' OR kind = 'RETURN_INVOICE_CONSENT'",
    )


def abort_if_tap_consents_exist(conn: Connection) -> None:
    """點選同意是法定的買受人同意證據；降版會把它們變成「手寫簽名但沒有簽名圖」，有就中止。"""
    n = conn.execute(
        sa.text("SELECT count(*) FROM signature_tasks WHERE consent_mode = 'TAP'")
    ).scalar_one()
    if n:
        raise RuntimeError(f"拒絕降版：已有點選同意的簽署紀錄 {n} 筆，降版後無法辨識其同意方式")


def downgrade() -> None:
    abort_if_tap_consents_exist(op.get_bind())
    op.drop_constraint(
        "ck_signature_tasks_tap_only_return_consent", "signature_tasks", type_="check"
    )
    op.drop_constraint("ck_signature_tasks_consent_mode", "signature_tasks", type_="check")
    op.drop_column("signature_tasks", "consent_mode")
