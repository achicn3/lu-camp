"""settings.auto_print_acquisition_labels：收購送出後自動印標籤（預設開）。"""

import sqlalchemy as sa
from alembic import op

revision = "f2c8a4d61e93"
down_revision = "e7b3c9d15a42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column(
            "auto_print_acquisition_labels",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("settings", "auto_print_acquisition_labels")
