"""agenda 資料存取（唯一能碰 agenda_* 表的層）。封存的一律不回傳。"""

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agenda.models import AgendaEvent, AgendaTask, AgendaTaskSeries
from app.shared.enums import AgendaTaskStatus


class AgendaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, row: AgendaTask | AgendaTaskSeries | AgendaEvent) -> None:
        self._session.add(row)
        await self._session.flush()

    async def add_all(self, rows: list[AgendaTask]) -> None:
        """一次寫入多筆（補建好幾年沒打勾的那幾次時，不要一筆一筆來回）。"""
        self._session.add_all(rows)
        await self._session.flush()

    # ── 待辦 ──────────────────────────────────────────────

    async def get_task(self, store_id: int, task_id: int) -> AgendaTask | None:
        task: AgendaTask | None = await self._session.scalar(
            select(AgendaTask).where(
                AgendaTask.store_id == store_id,
                AgendaTask.id == task_id,
                AgendaTask.archived_at.is_(None),
            )
        )
        return task

    async def get_occurrence(self, series_id: int, occurrence_on: date) -> AgendaTask | None:
        """某個重複待辦某一次的那一列（含封存的：唯一鍵不分封存與否）。"""
        task: AgendaTask | None = await self._session.scalar(
            select(AgendaTask).where(
                AgendaTask.series_id == series_id, AgendaTask.occurrence_on == occurrence_on
            )
        )
        return task

    async def tasks_due_between(self, store_id: int, start: date, end: date) -> list[AgendaTask]:
        rows = await self._session.scalars(
            select(AgendaTask)
            .where(
                AgendaTask.store_id == store_id,
                AgendaTask.archived_at.is_(None),
                AgendaTask.due_on >= start,
                AgendaTask.due_on <= end,
            )
            .order_by(AgendaTask.due_on, AgendaTask.id)
        )
        return list(rows.all())

    async def open_tasks_due_before(self, store_id: int, day: date) -> list[AgendaTask]:
        rows = await self._session.scalars(
            select(AgendaTask)
            .where(
                AgendaTask.store_id == store_id,
                AgendaTask.archived_at.is_(None),
                AgendaTask.status == AgendaTaskStatus.OPEN,
                AgendaTask.due_on < day,
            )
            .order_by(AgendaTask.due_on, AgendaTask.id)
        )
        return list(rows.all())

    async def undated_tasks(self, store_id: int, *, include_resolved: bool) -> list[AgendaTask]:
        stmt = select(AgendaTask).where(
            AgendaTask.store_id == store_id,
            AgendaTask.archived_at.is_(None),
            AgendaTask.due_on.is_(None),
        )
        if not include_resolved:
            stmt = stmt.where(AgendaTask.status == AgendaTaskStatus.OPEN)
        rows = await self._session.scalars(stmt.order_by(AgendaTask.id))
        return list(rows.all())

    async def open_rows_of_series(self, series_id: int) -> list[AgendaTask]:
        rows = await self._session.scalars(
            select(AgendaTask).where(
                AgendaTask.series_id == series_id,
                AgendaTask.archived_at.is_(None),
                AgendaTask.status == AgendaTaskStatus.OPEN,
            )
        )
        return list(rows.all())

    async def rows_of_series(self, series_id: int) -> list[AgendaTask]:
        """某個重複待辦已經建列的每一次（任何狀態、含封存），改整體設定時一起改用。"""
        rows = await self._session.scalars(
            select(AgendaTask).where(AgendaTask.series_id == series_id)
        )
        return list(rows.all())

    async def materialized_dates(self, series_ids: list[int]) -> dict[int, set[date]]:
        """各重複待辦已經建列的那幾次（任何狀態、含封存），算「還沒建列的」時要扣掉。"""
        found: dict[int, set[date]] = defaultdict(set)
        if not series_ids:
            return found
        rows = await self._session.execute(
            select(AgendaTask.series_id, AgendaTask.occurrence_on).where(
                AgendaTask.series_id.in_(series_ids)
            )
        )
        for series_id, occurrence_on in rows.all():
            if series_id is not None and occurrence_on is not None:
                found[series_id].add(occurrence_on)
        return found

    # ── 重複待辦的規則 ──────────────────────────────────────

    async def get_series(self, store_id: int, series_id: int) -> AgendaTaskSeries | None:
        series: AgendaTaskSeries | None = await self._session.scalar(
            select(AgendaTaskSeries).where(
                AgendaTaskSeries.store_id == store_id,
                AgendaTaskSeries.id == series_id,
                AgendaTaskSeries.archived_at.is_(None),
            )
        )
        return series

    async def active_series(self, store_id: int) -> list[AgendaTaskSeries]:
        rows = await self._session.scalars(
            select(AgendaTaskSeries)
            .where(AgendaTaskSeries.store_id == store_id, AgendaTaskSeries.archived_at.is_(None))
            .order_by(AgendaTaskSeries.id)
        )
        return list(rows.all())

    # ── 事件 ──────────────────────────────────────────────

    async def get_event(self, store_id: int, event_id: int) -> AgendaEvent | None:
        event: AgendaEvent | None = await self._session.scalar(
            select(AgendaEvent).where(
                AgendaEvent.store_id == store_id,
                AgendaEvent.id == event_id,
                AgendaEvent.archived_at.is_(None),
            )
        )
        return event

    async def active_events(self, store_id: int) -> list[AgendaEvent]:
        """未封存的事件（每店就幾十筆；重複事件的每一次在服務層算）。"""
        rows = await self._session.scalars(
            select(AgendaEvent)
            .where(AgendaEvent.store_id == store_id, AgendaEvent.archived_at.is_(None))
            .order_by(AgendaEvent.id)
        )
        return list(rows.all())
