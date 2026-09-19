"""signature_tasks.pos_terminal_id：簽署任務綁到推它的那台櫃檯（2026-09-19 審查 M1）

原本只記 `kiosk_device_id`。平板換配對（遺失、改配到收購櫃檯）後，舊櫃檯推的任務仍掛在
同一台平板上，新配對的畫面照樣讀得到、甚至簽得下去——連同切結書裡的姓名、電話與證號末碼。

本欄於建立任務時由當下的配對寫入；既有列自購物車回填（購物車本來就記了櫃檯），
無購物車的舊列留 NULL＝不限櫃檯，維持舊行為，不追溯改寫已簽證據。

Revision ID: e1a4c7d2b830
Revises: d7c3e5b19a20
Create Date: 2026-09-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1a4c7d2b830"
down_revision: str | Sequence[str] | None = "d7c3e5b19a20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 證據不可修改觸發器：歸屬欄多了 pos_terminal_id。函式本文在此逐字寫出（不 import
# models 的常數），舊 migration 才不會因為常數被後人改動而跟著變形。
_IMMUTABLE_FN = """
CREATE OR REPLACE FUNCTION signature_task_evidence_immutable() RETURNS trigger AS $$
BEGIN
  IF OLD.store_id IS DISTINCT FROM NEW.store_id
     OR OLD.kind IS DISTINCT FROM NEW.kind
     OR OLD.contact_id IS DISTINCT FROM NEW.contact_id
     OR OLD.kiosk_device_id IS DISTINCT FROM NEW.kiosk_device_id
     {terminal_clause}
     OR OLD.cart_session_id IS DISTINCT FROM NEW.cart_session_id
     OR OLD.content IS DISTINCT FROM NEW.content
     OR OLD.agreement_version_id IS DISTINCT FROM NEW.agreement_version_id
     OR OLD.cart_snapshot_fingerprint IS DISTINCT FROM NEW.cart_snapshot_fingerprint
     OR OLD.identity_fingerprint IS DISTINCT FROM NEW.identity_fingerprint
     OR OLD.ref_type IS DISTINCT FROM NEW.ref_type
     OR OLD.ref_id IS DISTINCT FROM NEW.ref_id
     OR OLD.created_by IS DISTINCT FROM NEW.created_by
     OR OLD.retention_policy IS DISTINCT FROM NEW.retention_policy THEN
    RAISE EXCEPTION 'signature_tasks 的簽署內容與歸屬不可修改';
  END IF;
  IF OLD.signed_at IS NOT NULL THEN
    IF OLD.signed_at IS DISTINCT FROM NEW.signed_at
       OR OLD.chosen_payout IS DISTINCT FROM NEW.chosen_payout
       OR OLD.signature_sha256 IS DISTINCT FROM NEW.signature_sha256
       OR OLD.content_sha256 IS DISTINCT FROM NEW.content_sha256
       OR OLD.evidence_hash IS DISTINCT FROM NEW.evidence_hash
       OR OLD.sign_idempotency_key IS DISTINCT FROM NEW.sign_idempotency_key
       OR OLD.signature_retention_until IS DISTINCT FROM NEW.signature_retention_until THEN
      RAISE EXCEPTION 'signature_tasks 的已封存證據不可修改';
    END IF;
    IF (OLD.signature_image IS NULL AND NEW.signature_image IS NOT NULL)
       OR (OLD.signature_image IS NOT NULL AND NEW.signature_image IS NOT NULL
           AND OLD.signature_image IS DISTINCT FROM NEW.signature_image) THEN
      RAISE EXCEPTION '簽名 PNG 封存後只能依保存政策清除，不可替換或還原';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_TERMINAL_CLAUSE = "OR OLD.pos_terminal_id IS DISTINCT FROM NEW.pos_terminal_id"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("signature_tasks", sa.Column("pos_terminal_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_signature_tasks_pos_terminal_id",
        "signature_tasks",
        ["pos_terminal_id"],
    )
    # 回填要在掛上「歸屬不可修改」之前做完——之後這欄就鎖死了。
    op.execute(
        """
        UPDATE signature_tasks AS t
        SET pos_terminal_id = c.pos_terminal_id
        FROM cart_sessions AS c
        WHERE t.cart_session_id = c.id
          AND t.store_id = c.store_id
          AND t.pos_terminal_id IS NULL
        """
    )
    op.create_foreign_key(
        "fk_signature_tasks_pos_terminal_store",
        "signature_tasks",
        "pos_terminals",
        ["pos_terminal_id", "store_id"],
        ["id", "store_id"],
    )
    op.execute(_IMMUTABLE_FN.format(terminal_clause=_TERMINAL_CLAUSE))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(_IMMUTABLE_FN.format(terminal_clause=""))
    op.drop_constraint(
        "fk_signature_tasks_pos_terminal_store",
        "signature_tasks",
        type_="foreignkey",
    )
    op.drop_index("ix_signature_tasks_pos_terminal_id", table_name="signature_tasks")
    op.drop_column("signature_tasks", "pos_terminal_id")
