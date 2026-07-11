"""einvoice_upload_queue：認領時凍結的 Amego data JSON（重送 byte-for-byte）

docs/24（Codex 第二輪）：已認領（可能已曝光平台）的重送不得以「活狀態」重建 payload——
稅率變更/跨日會讓同一 OrderId/AllowanceNumber 送出不同內容。認領當下把 data JSON 全文
持久化，重送一律用凍結內容並以 xml_sha256 驗證。

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e0f1a2b3c4"
down_revision: str | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("einvoice_upload_queue", sa.Column("amego_payload", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("einvoice_upload_queue", "amego_payload")
