"""signature_tasks.identity_fingerprint（綁定用穩定身分指紋，伺服器內部欄，docs/23 K4 第十一輪）

穩定身分指紋 = national_id_blind_index（HMAC）。原存於 content JSON 會被手持端 API 讀到、
洩漏可跨任務關聯的 HMAC 身分（跨 D1/D4 PII 邊界）；改存本內部欄，絕不列入任何讀取序列化。

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-07-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5a6b7c8d9e0"
down_revision: str | Sequence[str] | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "signature_tasks",
        sa.Column("identity_fingerprint", sa.String(length=64), nullable=True),
    )
    # 版本銜接（Codex K4 第十二輪 high）：舊 K4 碼把指紋存在 content JSON，直接改讀新欄會讓
    # 那些列 (a) 仍經序列化外洩指紋、(b) 因新欄為 NULL 而無法綁定。故：先把 content 內的
    # national_id_fingerprint 回填至內部欄，再自 content 移除該鍵（用 jsonb_exists 避免 `?`
    # 被 SQLAlchemy 當成綁定參數）。
    op.execute(
        """
        UPDATE signature_tasks
        SET identity_fingerprint = content->>'national_id_fingerprint'
        WHERE identity_fingerprint IS NULL
          AND jsonb_exists(content, 'national_id_fingerprint')
        """
    )
    op.execute(
        """
        UPDATE signature_tasks
        SET content = content - 'national_id_fingerprint'
        WHERE jsonb_exists(content, 'national_id_fingerprint')
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    # 還原：把內部欄的指紋放回 content（維持可逆），再移除欄位。
    op.execute(
        """
        UPDATE signature_tasks
        SET content = jsonb_set(
            content, '{national_id_fingerprint}', to_jsonb(identity_fingerprint)
        )
        WHERE identity_fingerprint IS NOT NULL
        """
    )
    op.drop_column("signature_tasks", "identity_fingerprint")
