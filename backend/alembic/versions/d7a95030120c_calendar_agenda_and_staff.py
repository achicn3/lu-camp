"""行事曆 1A：員工名單、待辦、重複待辦、事件（docs/99、ADR-034）。

- staff_members：員工名單（不一定有系統帳號；一個帳號只對到一位員工）。
- agenda_task_series：重複待辦的規則（不預先展開）。
- agenda_tasks：單次待辦，與重複待辦被打勾／略過／修改過的那一次。
- agenda_events：公休、臨時店休、市集等事件（可重複、可取消某一次）。

降版：已有資料時拒絕（店員記的待辦會整批消失）。

Revision ID: d7a95030120c
Revises: c3e9a7d5b1f4
"""

from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d7a95030120c"
down_revision = "c3e9a7d5b1f4"
branch_labels = None
depends_on = None

_RECURRENCE_KINDS = ("WEEKLY", "MONTHLY", "EVERY_N_MONTHS", "YEARLY")
_TASK_STATUSES = ("OPEN", "DONE", "SKIPPED")
_EVENT_KINDS = ("CLOSED", "TEMP_CLOSED", "MARKET", "OTHER")


def _enum_col(*values: str) -> sa.Enum:
    return sa.Enum(*values, native_enum=False, length=30, create_constraint=True)


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def _rule_columns(*, kind_nullable: bool) -> list[sa.Column[Any]]:
    return [
        sa.Column("rule_kind", _enum_col(*_RECURRENCE_KINDS), nullable=kind_nullable),
        sa.Column(
            "weekdays",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("interval_months", sa.Integer(), nullable=True),
        sa.Column("month_of_year", sa.Integer(), nullable=True),
    ]


def _rule_checks(table: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(
            "day_of_month IS NULL OR day_of_month BETWEEN 1 AND 31", name=f"ck_{table}_day"
        ),
        sa.CheckConstraint(
            "interval_months IS NULL OR interval_months BETWEEN 2 AND 24",
            name=f"ck_{table}_interval",
        ),
        sa.CheckConstraint(
            "month_of_year IS NULL OR month_of_year BETWEEN 1 AND 12", name=f"ck_{table}_month"
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "staff_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        *_timestamps(),
        sa.UniqueConstraint("store_id", "name", name="uq_staff_members_store_name"),
        sa.UniqueConstraint("store_id", "user_id", name="uq_staff_members_store_user"),
    )
    op.create_index("ix_staff_members_store_id", "staff_members", ["store_id"])

    op.create_table(
        "agenda_task_series",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("due_time", sa.Time(), nullable=True),
        sa.Column(
            "assignee_staff_id", sa.Integer(), sa.ForeignKey("staff_members.id"), nullable=True
        ),
        sa.Column("manager_only", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("link_href", sa.String(200), nullable=True),
        sa.Column("lead_days", sa.Integer(), nullable=False, server_default=sa.text("3")),
        *_rule_columns(kind_nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("lead_days BETWEEN 0 AND 365", name="ck_agenda_task_series_lead_days"),
        sa.CheckConstraint(
            "ends_on IS NULL OR ends_on >= starts_on", name="ck_agenda_task_series_window"
        ),
        *_rule_checks("agenda_task_series"),
    )
    op.create_index("ix_agenda_task_series_store_id", "agenda_task_series", ["store_id"])

    op.create_table(
        "agenda_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("due_time", sa.Time(), nullable=True),
        sa.Column(
            "assignee_staff_id", sa.Integer(), sa.ForeignKey("staff_members.id"), nullable=True
        ),
        sa.Column("manager_only", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("link_href", sa.String(200), nullable=True),
        sa.Column("lead_days", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("series_id", sa.Integer(), sa.ForeignKey("agenda_task_series.id"), nullable=True),
        sa.Column("occurrence_on", sa.Date(), nullable=True),
        sa.Column(
            "status",
            _enum_col(*_TASK_STATUSES),
            nullable=False,
            server_default="OPEN",
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("series_id", "occurrence_on", name="uq_agenda_tasks_occurrence"),
        sa.CheckConstraint(
            "(series_id IS NULL) = (occurrence_on IS NULL)",
            name="ck_agenda_tasks_occurrence_pair",
        ),
        sa.CheckConstraint(
            "series_id IS NULL OR due_on IS NOT NULL", name="ck_agenda_tasks_occurrence_dated"
        ),
        sa.CheckConstraint(
            "due_time IS NULL OR due_on IS NOT NULL", name="ck_agenda_tasks_time_needs_date"
        ),
        sa.CheckConstraint(
            "(status = 'OPEN') = (resolved_at IS NULL)", name="ck_agenda_tasks_resolved"
        ),
        sa.CheckConstraint("lead_days BETWEEN 0 AND 365", name="ck_agenda_tasks_lead_days"),
    )
    op.create_index("ix_agenda_tasks_store_id", "agenda_tasks", ["store_id"])
    op.create_index("ix_agenda_tasks_series_id", "agenda_tasks", ["series_id"])
    op.create_index("ix_agenda_tasks_store_due_on", "agenda_tasks", ["store_id", "due_on"])

    op.create_table(
        "agenda_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("store_id", sa.Integer(), sa.ForeignKey("stores.id"), nullable=False),
        sa.Column("kind", _enum_col(*_EVENT_KINDS), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("manager_only", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        *_rule_columns(kind_nullable=True),
        sa.Column("repeat_until", sa.Date(), nullable=True),
        sa.Column(
            "cancelled_dates",
            postgresql.ARRAY(sa.Date()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_agenda_events_window"),
        sa.CheckConstraint("ends_on - starts_on <= 366", name="ck_agenda_events_length"),
        sa.CheckConstraint(
            "repeat_until IS NULL OR (rule_kind IS NOT NULL AND repeat_until >= starts_on)",
            name="ck_agenda_events_repeat_until",
        ),
        *_rule_checks("agenda_events"),
    )
    op.create_index("ix_agenda_events_store_id", "agenda_events", ["store_id"])


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("agenda_tasks", "agenda_task_series", "agenda_events", "staff_members"):
        if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar():
            raise RuntimeError(
                f"{table} 已有資料，降版會整批刪除行事曆與員工名單；"
                "請先確認不再需要後手動清空，或只退程式、不降 schema"
            )
    op.drop_table("agenda_tasks")
    op.drop_table("agenda_task_series")
    op.drop_table("agenda_events")
    op.drop_table("staff_members")
