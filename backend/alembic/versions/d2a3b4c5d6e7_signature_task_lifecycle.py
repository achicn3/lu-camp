"""客顯簽署任務狀態機、證據 hash、裝置歸屬與 PNG 保存報表設定。

Revision ID: d2a3b4c5d6e7
Revises: d1f2a3b4c5d6
Create Date: 2026-07-24 00:00:00.000000
"""

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

from app.core.canonical import canonical_json_bytes
from app.modules.signing.models import (
    SIGNATURE_TASK_EVENT_IMMUTABLE_DDL,
    SIGNATURE_TASK_EVENT_IMMUTABLE_DROP_DDL,
    SIGNATURE_TASK_EVIDENCE_IMMUTABLE_DDL,
    SIGNATURE_TASK_EVIDENCE_IMMUTABLE_DROP_DDL,
)

revision: str = "d2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "d1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_VALUES = (
    "PENDING",
    "SIGNING",
    "SIGNED",
    "CONSUMED",
    "VOIDED",
    "EXPIRED",
    "FAILED",
)


def _hash_existing_evidence() -> None:
    """舊簽署證據補 canonical hash；不改動既有 content 與 PNG bytes。"""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT st.id, st.store_id, st.kind, st.status, st.content, st.ref_id,
                   st.signature_image, st.signed_at,
                   sale.id AS bound_sale_id,
                   ack_sale.id AS ack_sale_id,
                   COALESCE(s.signature_png_retention_days, 183) AS retention_days,
                   CASE
                     WHEN a.id IS NOT NULL OR sale.id IS NOT NULL
                       OR (st.kind = 'TRANSACTION_ACK' AND ack_sale.id IS NOT NULL)
                     THEN true ELSE false
                   END AS is_bound
              FROM signature_tasks st
              LEFT JOIN settings s ON s.store_id = st.store_id
              LEFT JOIN acquisitions a ON a.signature_task_id = st.id
              LEFT JOIN sales sale ON sale.signature_task_id = st.id
              LEFT JOIN sales ack_sale
                ON st.kind = 'TRANSACTION_ACK'
               AND lower(st.ref_type) = 'sale'
               AND ack_sale.id = st.ref_id
               AND ack_sale.store_id = st.store_id
            """
        )
    ).mappings()
    for row in rows:
        old_status = str(row["status"])
        new_status = old_status
        now = datetime.now(UTC)
        values: dict[str, object] = {}
        if old_status == "CANCELLED":
            new_status = "VOIDED"
            values["voided_at"] = now
        elif old_status == "PENDING":
            # 舊任務沒有裝置／購物車歸屬，不能安全送往多櫃檯，遷移時作廢留證。
            new_status = "VOIDED"
            values["voided_at"] = now
        elif old_status == "SIGNED":
            content_sha = hashlib.sha256(canonical_json_bytes(row["content"])).hexdigest()
            image = bytes(row["signature_image"])
            signature_sha = hashlib.sha256(image).hexdigest()
            signed_at = row["signed_at"]
            assert signed_at is not None
            evidence_hash = hashlib.sha256(
                canonical_json_bytes(
                    {
                        "task_id": row["id"],
                        "content_sha256": content_sha,
                        "signature_sha256": signature_sha,
                        "signed_at": signed_at.isoformat(),
                    }
                )
            ).hexdigest()
            values.update(
                {
                    "content_sha256": content_sha,
                    "signature_sha256": signature_sha,
                    "evidence_hash": evidence_hash,
                    "signature_retention_until": signed_at
                    + timedelta(days=int(row["retention_days"])),
                }
            )
            if bool(row["is_bound"]):
                new_status = "CONSUMED"
                values["consumed_at"] = signed_at
            else:
                values["expires_at"] = signed_at + timedelta(minutes=5)
        values["status"] = new_status
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        bind.execute(
            sa.text(f"UPDATE signature_tasks SET {assignments} WHERE id = :task_id"),
            {**values, "task_id": row["id"]},
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO signature_task_events
                    (store_id, signature_task_id, from_status, to_status,
                     reason_code, reason_detail, sale_id, created_at)
                VALUES
                    (:store_id, :task_id, :from_status, :to_status,
                     'MIGRATION_BACKFILL', :detail, :sale_id, now())
                """
            ),
            {
                "store_id": row["store_id"],
                "task_id": row["id"],
                # CANCELLED 不在新 enum，舊狀態保留於說明，事件起點以 NULL 表示遷移導入。
                "from_status": None if old_status == "CANCELLED" else old_status,
                "to_status": new_status,
                "detail": (
                    "既有已綁定簽署補為 CONSUMED"
                    if old_status == "SIGNED" and new_status == "CONSUMED"
                    else "既有簽署任務遷移至裝置綁定狀態機"
                ),
                "sale_id": row["bound_sale_id"] or row["ack_sale_id"],
            },
        )


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column(
            "signature_png_retention_days",
            sa.Integer(),
            server_default=sa.text("183"),
            nullable=False,
        ),
    )
    op.add_column(
        "settings",
        sa.Column(
            "signature_cleanup_enforcement_mode",
            sa.String(length=20),
            server_default=sa.text("'REPORT_ONLY'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_settings_signature_png_retention_days",
        "settings",
        "signature_png_retention_days BETWEEN 1 AND 3650",
    )
    op.create_check_constraint(
        "ck_settings_signature_cleanup_report_only",
        "settings",
        "signature_cleanup_enforcement_mode = 'REPORT_ONLY'",
    )

    op.drop_index("uq_signature_tasks_store_pending", table_name="signature_tasks")
    op.drop_constraint("signaturetaskstatus", "signature_tasks", type_="check")
    op.drop_constraint(
        "ck_signature_tasks_signed_evidence",
        "signature_tasks",
        type_="check",
    )
    op.drop_constraint(
        "ck_signature_tasks_signed_affidavit_payout",
        "signature_tasks",
        type_="check",
    )
    op.alter_column("signature_tasks", "cancelled_at", new_column_name="voided_at")
    op.add_column("signature_tasks", sa.Column("kiosk_device_id", sa.Integer(), nullable=True))
    op.add_column("signature_tasks", sa.Column("cart_session_id", sa.Integer(), nullable=True))
    op.add_column("signature_tasks", sa.Column("signature_sha256", sa.String(64), nullable=True))
    op.add_column("signature_tasks", sa.Column("content_sha256", sa.String(64), nullable=True))
    op.add_column("signature_tasks", sa.Column("evidence_hash", sa.String(64), nullable=True))
    op.add_column(
        "signature_tasks",
        sa.Column("cart_snapshot_fingerprint", sa.String(64), nullable=True),
    )
    op.add_column(
        "signature_tasks",
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_tasks",
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_tasks",
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_tasks",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_tasks",
        sa.Column("last_user_activity_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_tasks",
        sa.Column("failure_reason", sa.String(300), nullable=True),
    )
    op.add_column(
        "signature_tasks",
        sa.Column("signature_retention_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_tasks",
        sa.Column("signature_cleanup_reported_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "signature_tasks",
        sa.Column(
            "retention_policy",
            sa.String(40),
            server_default=sa.text("'TRANSACTION_RECORD_5Y'"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_signature_tasks_id_store",
        "signature_tasks",
        ["id", "store_id"],
    )
    op.create_foreign_key(
        "fk_signature_tasks_kiosk_device_store",
        "signature_tasks",
        "kiosk_devices",
        ["kiosk_device_id", "store_id"],
        ["id", "store_id"],
    )
    op.create_foreign_key(
        "fk_signature_tasks_cart_session_store",
        "signature_tasks",
        "cart_sessions",
        ["cart_session_id", "store_id"],
        ["id", "store_id"],
        use_alter=True,
    )
    for column in (
        "kiosk_device_id",
        "cart_session_id",
        "expires_at",
        "signature_retention_until",
    ):
        op.create_index(f"ix_signature_tasks_{column}", "signature_tasks", [column])

    op.add_column(
        "cart_sessions",
        sa.Column("active_signature_task_id", sa.Integer(), nullable=True),
    )
    op.add_column("cart_sessions", sa.Column("sale_id", sa.Integer(), nullable=True))
    op.add_column(
        "cart_sessions",
        sa.Column("payment_order_id", sa.String(80), nullable=True),
    )
    op.add_column(
        "cart_sessions",
        sa.Column("payment_uncertain_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "cart_sessions",
        sa.Column("payment_uncertain_reason", sa.String(300), nullable=True),
    )
    op.add_column(
        "cart_sessions",
        sa.Column("payment_checkout_payload", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    op.create_index(
        "ix_cart_sessions_active_signature_task_id",
        "cart_sessions",
        ["active_signature_task_id"],
    )
    op.create_foreign_key(
        "fk_cart_sessions_active_signature_task_store",
        "cart_sessions",
        "signature_tasks",
        ["active_signature_task_id", "store_id"],
        ["id", "store_id"],
        use_alter=True,
    )
    op.create_index("ix_cart_sessions_sale_id", "cart_sessions", ["sale_id"])
    op.create_index(
        "ix_cart_sessions_payment_order_id",
        "cart_sessions",
        ["payment_order_id"],
    )
    op.create_unique_constraint(
        "uq_cart_sessions_sale_id",
        "cart_sessions",
        ["sale_id"],
    )
    op.create_foreign_key(
        "fk_cart_sessions_sale_store",
        "cart_sessions",
        "sales",
        ["sale_id", "store_id"],
        ["id", "store_id"],
    )

    op.create_table(
        "signature_task_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("signature_task_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(30), nullable=True),
        sa.Column("to_status", sa.String(30), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column(
            "actor_kiosk_device_id",
            sa.Integer(),
            sa.ForeignKey("kiosk_devices.id"),
            nullable=True,
        ),
        sa.Column("reason_code", sa.String(60), nullable=False),
        sa.Column("reason_detail", sa.String(300), nullable=True),
        sa.Column("cart_session_id", sa.Integer(), sa.ForeignKey("cart_sessions.id")),
        sa.Column("sale_id", sa.Integer()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["signature_task_id", "store_id"],
            ["signature_tasks.id", "signature_tasks.store_id"],
            name="fk_signature_task_events_task_store",
        ),
        sa.ForeignKeyConstraint(
            ["sale_id", "store_id"],
            ["sales.id", "sales.store_id"],
            name="fk_signature_task_events_sale_store",
        ),
    )
    op.create_index(
        "ix_signature_task_events_store_id",
        "signature_task_events",
        ["store_id"],
    )
    op.create_index(
        "ix_signature_task_events_signature_task_id",
        "signature_task_events",
        ["signature_task_id"],
    )

    _hash_existing_evidence()

    op.create_check_constraint(
        "signaturetaskstatus",
        "signature_tasks",
        "status IN (" + ",".join(f"'{value}'" for value in _STATUS_VALUES) + ")",
    )
    op.create_check_constraint(
        "ck_signature_tasks_signed_evidence",
        "signature_tasks",
        "status NOT IN ('SIGNED','CONSUMED','FAILED') OR "
        "(signed_at IS NOT NULL AND signature_sha256 IS NOT NULL "
        "AND content_sha256 IS NOT NULL "
        "AND evidence_hash IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_signature_tasks_signed_affidavit_payout",
        "signature_tasks",
        "NOT (status IN ('SIGNED','CONSUMED','FAILED') "
        "AND kind = 'ACQUISITION_AFFIDAVIT') OR chosen_payout IS NOT NULL",
    )
    op.create_index(
        "uq_signature_tasks_active_kiosk",
        "signature_tasks",
        ["kiosk_device_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING','SIGNING')"),
    )
    op.create_index(
        "uq_signature_tasks_active_cart",
        "signature_tasks",
        ["cart_session_id"],
        unique=True,
        postgresql_where=sa.text(
            "cart_session_id IS NOT NULL AND status IN ('PENDING','SIGNING','SIGNED')"
        ),
    )
    for ddl in SIGNATURE_TASK_EVENT_IMMUTABLE_DDL:
        op.execute(ddl)
    for ddl in SIGNATURE_TASK_EVIDENCE_IMMUTABLE_DDL:
        op.execute(ddl)


def downgrade() -> None:
    for ddl in SIGNATURE_TASK_EVIDENCE_IMMUTABLE_DROP_DDL:
        op.execute(ddl)
    for ddl in SIGNATURE_TASK_EVENT_IMMUTABLE_DROP_DDL:
        op.execute(ddl)
    op.drop_index("uq_signature_tasks_active_cart", table_name="signature_tasks")
    op.drop_index("uq_signature_tasks_active_kiosk", table_name="signature_tasks")
    op.drop_constraint(
        "ck_signature_tasks_signed_affidavit_payout",
        "signature_tasks",
        type_="check",
    )
    op.drop_constraint(
        "ck_signature_tasks_signed_evidence",
        "signature_tasks",
        type_="check",
    )
    op.drop_constraint("signaturetaskstatus", "signature_tasks", type_="check")

    # 舊 schema 同店只能有一張 PENDING；其餘活動任務降級為 CANCELLED。
    op.execute(
        """
        WITH ranked AS (
          SELECT id, row_number() OVER (PARTITION BY store_id ORDER BY id DESC) AS rn
            FROM signature_tasks
           WHERE status IN ('PENDING','SIGNING')
        )
        UPDATE signature_tasks st
           SET status = CASE WHEN ranked.rn = 1 THEN 'PENDING' ELSE 'VOIDED' END,
               voided_at = CASE WHEN ranked.rn = 1 THEN voided_at ELSE now() END
          FROM ranked
         WHERE st.id = ranked.id
        """
    )
    op.execute("UPDATE signature_tasks SET status = 'SIGNED' WHERE status = 'CONSUMED'")
    op.execute(
        """
        UPDATE signature_tasks
           SET status = 'CANCELLED',
               voided_at = COALESCE(voided_at, expired_at, failed_at, now())
         WHERE status IN ('VOIDED','EXPIRED','FAILED')
        """
    )

    op.drop_constraint(
        "fk_cart_sessions_active_signature_task_store",
        "cart_sessions",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_cart_sessions_active_signature_task_id",
        table_name="cart_sessions",
    )
    op.drop_column("cart_sessions", "active_signature_task_id")
    op.drop_constraint("fk_cart_sessions_sale_store", "cart_sessions", type_="foreignkey")
    op.drop_constraint("uq_cart_sessions_sale_id", "cart_sessions", type_="unique")
    op.drop_index("ix_cart_sessions_sale_id", table_name="cart_sessions")
    op.drop_column("cart_sessions", "sale_id")
    op.drop_index("ix_cart_sessions_payment_order_id", table_name="cart_sessions")
    op.drop_column("cart_sessions", "payment_uncertain_reason")
    op.drop_column("cart_sessions", "payment_checkout_payload")
    op.drop_column("cart_sessions", "payment_uncertain_at")
    op.drop_column("cart_sessions", "payment_order_id")

    op.drop_index(
        "ix_signature_task_events_signature_task_id",
        table_name="signature_task_events",
    )
    op.drop_index("ix_signature_task_events_store_id", table_name="signature_task_events")
    op.drop_table("signature_task_events")

    op.drop_constraint(
        "fk_signature_tasks_cart_session_store",
        "signature_tasks",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_signature_tasks_kiosk_device_store",
        "signature_tasks",
        type_="foreignkey",
    )
    op.drop_constraint("uq_signature_tasks_id_store", "signature_tasks", type_="unique")
    for column in (
        "signature_retention_until",
        "expires_at",
        "cart_session_id",
        "kiosk_device_id",
    ):
        op.drop_index(f"ix_signature_tasks_{column}", table_name="signature_tasks")
    for column in (
        "retention_policy",
        "signature_cleanup_reported_at",
        "signature_retention_until",
        "failure_reason",
        "last_user_activity_at",
        "expires_at",
        "failed_at",
        "expired_at",
        "consumed_at",
        "cart_snapshot_fingerprint",
        "evidence_hash",
        "content_sha256",
        "signature_sha256",
        "cart_session_id",
        "kiosk_device_id",
    ):
        op.drop_column("signature_tasks", column)
    op.alter_column("signature_tasks", "voided_at", new_column_name="cancelled_at")
    op.create_check_constraint(
        "signaturetaskstatus",
        "signature_tasks",
        "status IN ('PENDING','SIGNED','CANCELLED')",
    )
    op.create_check_constraint(
        "ck_signature_tasks_signed_evidence",
        "signature_tasks",
        "status <> 'SIGNED' OR (signature_image IS NOT NULL AND signed_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_signature_tasks_signed_affidavit_payout",
        "signature_tasks",
        "NOT (status = 'SIGNED' AND kind = 'ACQUISITION_AFFIDAVIT') OR chosen_payout IS NOT NULL",
    )
    op.create_index(
        "uq_signature_tasks_store_pending",
        "signature_tasks",
        ["store_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.drop_constraint(
        "ck_settings_signature_cleanup_report_only",
        "settings",
        type_="check",
    )
    op.drop_constraint(
        "ck_settings_signature_png_retention_days",
        "settings",
        type_="check",
    )
    op.drop_column("settings", "signature_cleanup_enforcement_mode")
    op.drop_column("settings", "signature_png_retention_days")
