"""agenda 模型：重複待辦的規則、每一筆待辦、事件（docs/99 §4、§12.2；ADR-034 決定 6）。

重複待辦**不預先展開**：`agenda_task_series` 只存規則；某一次被打勾、略過或修改時才在
`agenda_tasks` 建一列（`series_id`＋`occurrence_on` 唯一）。沒建列的過去日期視為已過期，
未來的在彙整時算出來顯示。單次待辦也存在 `agenda_tasks`（`series_id` 為空）。

刪除一律封存（`archived_at`）：已完成的歷史紀錄留著，比照開店前檢查。
"""

from datetime import date, datetime, time

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import AgendaEventKind, AgendaTaskStatus, RecurrenceKind


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class AgendaTaskSeries(Base, TimestampMixin):
    """重複待辦的規則：從 `starts_on` 起依規則出現；`ends_on` 為最後可能的一天（含）。"""

    __tablename__ = "agenda_task_series"
    __table_args__ = (
        CheckConstraint("lead_days BETWEEN 0 AND 365", name="ck_agenda_task_series_lead_days"),
        CheckConstraint(
            "ends_on IS NULL OR ends_on >= starts_on", name="ck_agenda_task_series_window"
        ),
        CheckConstraint(
            "day_of_month IS NULL OR day_of_month BETWEEN 1 AND 31",
            name="ck_agenda_task_series_day",
        ),
        CheckConstraint(
            "interval_months IS NULL OR interval_months BETWEEN 2 AND 24",
            name="ck_agenda_task_series_interval",
        ),
        CheckConstraint(
            "month_of_year IS NULL OR month_of_year BETWEEN 1 AND 12",
            name="ck_agenda_task_series_month",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(Text)
    due_time: Mapped[time | None] = mapped_column(Time)
    assignee_staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff_members.id"))
    manager_only: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    link_href: Mapped[str | None] = mapped_column(String(200))
    lead_days: Mapped[int] = mapped_column(Integer, default=3, server_default=text("3"))
    rule_kind: Mapped[RecurrenceKind] = mapped_column(_enum_col(RecurrenceKind))
    weekdays: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default=text("'{}'"))
    day_of_month: Mapped[int | None] = mapped_column(Integer)
    interval_months: Mapped[int | None] = mapped_column(Integer)
    month_of_year: Mapped[int | None] = mapped_column(Integer)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class AgendaTask(Base, TimestampMixin):
    """一筆待辦：單次的（`series_id` 為空），或重複待辦被打勾／略過／修改過的那一次。

    `occurrence_on` 是「規則上的哪一次」，`due_on` 是實際到期日（這一次可以改期）。
    OPEN 以外的狀態一定記下是誰、何時處理的（`resolved_*`）。
    """

    __tablename__ = "agenda_tasks"
    __table_args__ = (
        UniqueConstraint("series_id", "occurrence_on", name="uq_agenda_tasks_occurrence"),
        CheckConstraint(
            "(series_id IS NULL) = (occurrence_on IS NULL)",
            name="ck_agenda_tasks_occurrence_pair",
        ),
        CheckConstraint(
            "series_id IS NULL OR due_on IS NOT NULL", name="ck_agenda_tasks_occurrence_dated"
        ),
        CheckConstraint(
            "due_time IS NULL OR due_on IS NOT NULL", name="ck_agenda_tasks_time_needs_date"
        ),
        CheckConstraint(
            "(status = 'OPEN') = (resolved_at IS NULL)", name="ck_agenda_tasks_resolved"
        ),
        CheckConstraint("lead_days BETWEEN 0 AND 365", name="ck_agenda_tasks_lead_days"),
        Index("ix_agenda_tasks_store_due_on", "store_id", "due_on"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(Text)
    due_on: Mapped[date | None] = mapped_column(Date)
    due_time: Mapped[time | None] = mapped_column(Time)
    assignee_staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff_members.id"))
    manager_only: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    link_href: Mapped[str | None] = mapped_column(String(200))
    lead_days: Mapped[int] = mapped_column(Integer, default=3, server_default=text("3"))
    series_id: Mapped[int | None] = mapped_column(ForeignKey("agenda_task_series.id"), index=True)
    occurrence_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[AgendaTaskStatus] = mapped_column(
        _enum_col(AgendaTaskStatus),
        default=AgendaTaskStatus.OPEN,
        server_default=AgendaTaskStatus.OPEN.value,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))


class AgendaEvent(Base, TimestampMixin):
    """事件（公休、臨時店休、市集…）：只顯示，不影響 POS 與線上點餐（docs/99 §4.3）。

    `starts_on`～`ends_on` 是第一次的起訖（可跨天）；有重複規則時以 `starts_on` 為起點，
    每一次都跨同樣天數，最後一次不晚於 `repeat_until`。`cancelled_dates` 是被取消的那幾次的開始日。
    """

    __tablename__ = "agenda_events"
    __table_args__ = (
        CheckConstraint("ends_on >= starts_on", name="ck_agenda_events_window"),
        CheckConstraint("ends_on - starts_on <= 366", name="ck_agenda_events_length"),
        CheckConstraint(
            "repeat_until IS NULL OR (rule_kind IS NOT NULL AND repeat_until >= starts_on)",
            name="ck_agenda_events_repeat_until",
        ),
        CheckConstraint(
            "day_of_month IS NULL OR day_of_month BETWEEN 1 AND 31",
            name="ck_agenda_events_day",
        ),
        CheckConstraint(
            "interval_months IS NULL OR interval_months BETWEEN 2 AND 24",
            name="ck_agenda_events_interval",
        ),
        CheckConstraint(
            "month_of_year IS NULL OR month_of_year BETWEEN 1 AND 12",
            name="ck_agenda_events_month",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    kind: Mapped[AgendaEventKind] = mapped_column(_enum_col(AgendaEventKind))
    title: Mapped[str] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(Text)
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    start_time: Mapped[time | None] = mapped_column(Time)
    end_time: Mapped[time | None] = mapped_column(Time)
    manager_only: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    rule_kind: Mapped[RecurrenceKind | None] = mapped_column(_enum_col(RecurrenceKind))
    weekdays: Mapped[list[int]] = mapped_column(ARRAY(Integer), server_default=text("'{}'"))
    day_of_month: Mapped[int | None] = mapped_column(Integer)
    interval_months: Mapped[int | None] = mapped_column(Integer)
    month_of_year: Mapped[int | None] = mapped_column(Integer)
    repeat_until: Mapped[date | None] = mapped_column(Date)
    cancelled_dates: Mapped[list[date]] = mapped_column(ARRAY(Date), server_default=text("'{}'"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
