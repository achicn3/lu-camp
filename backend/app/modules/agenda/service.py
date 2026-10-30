"""agenda 業務邏輯（docs/99 §3、§4、§8；ADR-034 決定 6）。

- 重複待辦不預先展開：規則存在 `agenda_task_series`，某一次被打勾、略過或修改時才建列。
  過去沒建列的視為已過期，未來的在彙整時算出來。每一次獨立，上一次沒做不擋下一次。
- 改規則：之前沒做的那幾次先建成未完成的列（照舊提醒），新規則從今天起算。
- 「只限管理者」的項目店員看不到、也不能建立或改成只限管理者。
- 「今天」由呼叫端傳入（router 用門市時區的今天），邊界才測得準。
"""

from dataclasses import dataclass, replace
from datetime import date, time, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import store_date, store_day_bounds, store_period_end_day, utc_now
from app.modules.agenda.models import AgendaEvent, AgendaTask, AgendaTaskSeries
from app.modules.agenda.recurrence import (
    Recurrence,
    is_occurrence,
    occurrences_between,
    validate_recurrence,
)
from app.modules.agenda.repository import AgendaRepository
from app.modules.agenda.schemas import (
    MAX_EVENT_DAYS,
    MAX_FEED_DAYS,
    MAX_LEAD_DAYS,
    AgendaEventCreate,
    AgendaEventRead,
    AgendaEventUpdate,
    AgendaItemRead,
    AgendaOccurrenceUpdate,
    AgendaSeriesCreate,
    AgendaSeriesRead,
    AgendaSeriesUpdate,
    AgendaTaskCreate,
    AgendaTaskRead,
    AgendaTaskUpdate,
    AgendaTodayRead,
    RecurrenceFields,
)
from app.modules.campaigns.service import CampaignService
from app.modules.consignment.service import ConsignmentService
from app.modules.staff.service import StaffService
from app.modules.user.service import UserService
from app.shared.enums import (
    AgendaItemKind,
    AgendaTaskStatus,
    CampaignStatus,
    ConsignmentSettlementStatus,
)
from app.shared.exceptions import AgendaForbidden, AgendaItemInvalid, AgendaItemNotFound

# 找「下一次」時往後看多遠：每 24 個月一次的規則也找得到。
_NEXT_LOOKAHEAD = timedelta(days=800)
# 同一天裡的排序：公休等事件在最上面，再來活動，最後待辦。
_KIND_ORDER = {
    AgendaItemKind.EVENT: 0,
    AgendaItemKind.CAMPAIGN_START: 1,
    AgendaItemKind.CAMPAIGN_END: 2,
    AgendaItemKind.TASK: 3,
}
_TASK_FIELDS = (
    "title",
    "note",
    "due_on",
    "due_time",
    "assignee_staff_id",
    "manager_only",
    "link_href",
    "lead_days",
)
_SERIES_DISPLAY_FIELDS = (
    "title",
    "note",
    "due_time",
    "assignee_staff_id",
    "manager_only",
    "link_href",
    "lead_days",
)
_OCCURRENCE_FIELDS = ("due_on", "due_time", "note", "assignee_staff_id")
_EVENT_FIELDS = ("kind", "title", "note", "starts_on", "ends_on", "start_time", "end_time")
_CLERK_CAMPAIGN_STATUSES = {CampaignStatus.ACTIVE, CampaignStatus.ENDED}
_MANAGER_CAMPAIGN_STATUSES = {CampaignStatus.DRAFT, CampaignStatus.ACTIVE, CampaignStatus.ENDED}
_DAY = timedelta(days=1)


@dataclass(frozen=True)
class Viewer:
    """誰在看：決定「只限管理者」的項目要不要給、活動連結要不要給。"""

    user_id: int
    is_manager: bool

    def can_see(self, manager_only: bool) -> bool:
        return self.is_manager or not manager_only


def _rule(fields: RecurrenceFields, starts_on: date, ends_on: date | None = None) -> Recurrence:
    """畫面送來的規則 → 驗證過的規則（不合法拋 InvalidRecurrence）。"""
    return validate_recurrence(
        Recurrence(
            kind=fields.kind,
            starts_on=starts_on,
            ends_on=ends_on if ends_on is not None else fields.until,
            weekdays=tuple(fields.weekdays),
            day_of_month=fields.day_of_month,
            interval_months=fields.interval_months,
            month_of_year=fields.month_of_year,
        )
    )


def _series_rule(series: AgendaTaskSeries) -> Recurrence:
    return Recurrence(
        kind=series.rule_kind,
        starts_on=series.starts_on,
        ends_on=series.ends_on,
        weekdays=tuple(series.weekdays),
        day_of_month=series.day_of_month,
        interval_months=series.interval_months,
        month_of_year=series.month_of_year,
    )


def _event_rule(event: AgendaEvent) -> Recurrence | None:
    if event.rule_kind is None:
        return None
    return Recurrence(
        kind=event.rule_kind,
        starts_on=event.starts_on,
        ends_on=event.repeat_until,
        weekdays=tuple(event.weekdays),
        day_of_month=event.day_of_month,
        interval_months=event.interval_months,
        month_of_year=event.month_of_year,
    )


def _fields_of(rule: Recurrence) -> RecurrenceFields:
    return RecurrenceFields(
        kind=rule.kind,
        weekdays=list(rule.weekdays),
        day_of_month=rule.day_of_month,
        interval_months=rule.interval_months,
        month_of_year=rule.month_of_year,
        until=rule.ends_on,
    )


def _store_rule(target: AgendaTaskSeries | AgendaEvent, rule: Recurrence) -> None:
    """把規則的欄位寫回列上（開始、結束日由呼叫端各自處理：待辦與事件的欄位不同）。"""
    target.rule_kind = rule.kind
    target.weekdays = list(rule.weekdays)
    target.day_of_month = rule.day_of_month
    target.interval_months = rule.interval_months
    target.month_of_year = rule.month_of_year


def _clear_rule(event: AgendaEvent) -> None:
    """事件改成不重複：規則欄位清空，被取消的那幾次也沒有意義了。"""
    event.rule_kind = None
    event.weekdays = []
    event.day_of_month = None
    event.interval_months = None
    event.month_of_year = None
    event.repeat_until = None
    event.cancelled_dates = []


def _due(task: AgendaTask) -> date:
    """上行事曆的待辦一定有日期（查詢就是依日期查的）。"""
    if task.due_on is None:
        raise AgendaItemInvalid(f"待辦 {task.id} 沒有日期")
    return task.due_on


def _sort_key(item: AgendaItemRead) -> tuple[date, bool, time, int, str, str]:
    return (
        item.day,
        item.start_time is None,
        item.start_time or time.min,
        _KIND_ORDER[item.kind],
        item.title,
        item.key,
    )


class AgendaService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = AgendaRepository(session)
        self._staff = StaffService(session)

    # ── 單次待辦 ──────────────────────────────────────────

    async def create_task(
        self, store_id: int, viewer: Viewer, data: AgendaTaskCreate
    ) -> AgendaTaskRead:
        """新增單次待辦（沒有日期的放「沒有日期」清單）。"""
        self._guard_manager_only(viewer, data.manager_only)
        await self._check_assignee(store_id, data.assignee_staff_id)
        task = AgendaTask(
            store_id=store_id,
            title=data.title,
            note=data.note,
            due_on=data.due_on,
            due_time=data.due_time,
            assignee_staff_id=data.assignee_staff_id,
            manager_only=data.manager_only,
            link_href=data.link_href,
            lead_days=data.lead_days,
            created_by=viewer.user_id,
        )
        await self._repo.add(task)
        return await self._task_read(store_id, task)

    async def get_task(self, store_id: int, viewer: Viewer, task_id: int) -> AgendaTaskRead:
        return await self._task_read(store_id, await self._visible_task(store_id, viewer, task_id))

    async def update_task(
        self, store_id: int, viewer: Viewer, task_id: int, changes: AgendaTaskUpdate
    ) -> AgendaTaskRead:
        """改一筆待辦（單次的，或重複待辦已建列的那一次）；沒送的欄位不動。"""
        task = await self._visible_task(store_id, viewer, task_id)
        fields = changes.model_fields_set
        if "manager_only" in fields:
            self._guard_manager_only(viewer, bool(changes.manager_only))
            # 只限管理者是整個重複待辦的設定：單獨改某一次，經由規則的端點就會看得到它
            # （Codex 1A 第一輪 P1）。
            if task.series_id is not None and changes.manager_only != task.manager_only:
                raise AgendaItemInvalid(
                    "重複待辦的某一次不能單獨設定「只有管理者看得到」，請改整個重複待辦"
                )
        if "assignee_staff_id" in fields and changes.assignee_staff_id != task.assignee_staff_id:
            await self._check_assignee(store_id, changes.assignee_staff_id)
        due_on = changes.due_on if "due_on" in fields else task.due_on
        due_time = changes.due_time if "due_time" in fields else task.due_time
        if task.series_id is not None and due_on is None:
            raise AgendaItemInvalid("重複待辦的某一次一定要有日期")
        if due_time is not None and due_on is None:
            raise AgendaItemInvalid("有時間就要選日期")
        for name in _TASK_FIELDS:
            if name in fields:
                setattr(task, name, getattr(changes, name))
        await self._session.flush()
        return await self._task_read(store_id, task)

    async def set_task_status(
        self, store_id: int, viewer: Viewer, task_id: int, status: AgendaTaskStatus
    ) -> AgendaTaskRead:
        """完成／略過／取消完成；完成與略過記下是誰、何時。"""
        task = await self._visible_task(store_id, viewer, task_id)
        self._apply_status(task, status, viewer.user_id)
        await self._session.flush()
        return await self._task_read(store_id, task)

    async def archive_task(self, store_id: int, viewer: Viewer, task_id: int) -> None:
        """刪除單次待辦＝封存。重複待辦的某一次不能刪，要用略過（不然下次彙整又會算出來）。"""
        task = await self._visible_task(store_id, viewer, task_id)
        if task.series_id is not None:
            raise AgendaItemInvalid("重複待辦的某一次請用「略過」")
        task.archived_at = utc_now()
        await self._session.flush()

    async def list_undated(
        self, store_id: int, viewer: Viewer, *, include_resolved: bool = False
    ) -> list[AgendaTaskRead]:
        """「沒有日期」清單。"""
        rows = await self._repo.undated_tasks(store_id, include_resolved=include_resolved)
        return await self._task_reads(store_id, [r for r in rows if viewer.can_see(r.manager_only)])

    # ── 重複待辦 ──────────────────────────────────────────

    async def create_series(
        self,
        store_id: int,
        viewer: Viewer,
        data: AgendaSeriesCreate,
        *,
        today: date | None = None,
    ) -> AgendaSeriesRead:
        self._guard_manager_only(viewer, data.manager_only)
        await self._check_assignee(store_id, data.assignee_staff_id)
        rule = _rule(data.recurrence, data.starts_on)
        series = AgendaTaskSeries(
            store_id=store_id,
            title=data.title,
            note=data.note,
            due_time=data.due_time,
            assignee_staff_id=data.assignee_staff_id,
            manager_only=data.manager_only,
            link_href=data.link_href,
            lead_days=data.lead_days,
            starts_on=rule.starts_on,
            ends_on=rule.ends_on,
            created_by=viewer.user_id,
        )
        _store_rule(series, rule)
        await self._repo.add(series)
        return await self._series_read(store_id, series, today=today)

    async def get_series(
        self, store_id: int, viewer: Viewer, series_id: int, *, today: date | None = None
    ) -> AgendaSeriesRead:
        series = await self._visible_series(store_id, viewer, series_id)
        return await self._series_read(store_id, series, today=today)

    async def list_series(
        self, store_id: int, viewer: Viewer, *, today: date | None = None
    ) -> list[AgendaSeriesRead]:
        """所有重複待辦（管理用的清單）。"""
        visible = [
            s for s in await self._repo.active_series(store_id) if viewer.can_see(s.manager_only)
        ]
        return [await self._series_read(store_id, s, today=today) for s in visible]

    async def update_series(
        self,
        store_id: int,
        viewer: Viewer,
        series_id: int,
        changes: AgendaSeriesUpdate,
        *,
        today: date,
    ) -> AgendaSeriesRead:
        """改重複待辦。只改名稱等欄位不影響已經算出來的那幾次；改規則或開始日時，
        之前沒做的先建成未完成的列（照舊提醒），新規則從今天（或更晚的開始日）起算。"""
        series = await self._visible_series(store_id, viewer, series_id)
        fields = changes.model_fields_set
        if "manager_only" in fields:
            self._guard_manager_only(viewer, bool(changes.manager_only))
        if "assignee_staff_id" in fields and changes.assignee_staff_id != series.assignee_staff_id:
            await self._check_assignee(store_id, changes.assignee_staff_id)
        old_rule = _series_rule(series)
        requested = _rule(
            changes.recurrence if changes.recurrence is not None else _fields_of(old_rule),
            changes.starts_on if changes.starts_on is not None else old_rule.starts_on,
        )
        if replace(requested, ends_on=None) != replace(old_rule, ends_on=None):
            await self._keep_missed(series, old_rule, today=today, actor_user_id=viewer.user_id)
            new_rule = _rule(_fields_of(requested), max(requested.starts_on, today))
            _store_rule(series, new_rule)
            series.starts_on = new_rule.starts_on
            series.ends_on = new_rule.ends_on
        elif requested.ends_on != old_rule.ends_on:
            # 只改「重複到哪天」：排程不動、不重新起算。每幾個月是從開始月份數的，
            # 一重算就整個錯開（Codex 1A 第四輪 P2）。
            series.ends_on = requested.ends_on
        was_manager_only = series.manager_only
        for name in _SERIES_DISPLAY_FIELDS:
            if name in fields:
                setattr(series, name, getattr(changes, name))
        if series.manager_only != was_manager_only:
            # 已經建列的那幾次（做完、略過、改過的）跟著改，否則改成只限管理者之後，
            # 店員仍看得到以前那幾次的名稱與備註（Codex 1A 第一輪 P1）。
            for row in await self._repo.rows_of_series(series.id):
                row.manager_only = series.manager_only
        await self._session.flush()
        return await self._series_read(store_id, series, today=today)

    async def archive_series(self, store_id: int, viewer: Viewer, series_id: int) -> None:
        """刪除重複待辦：之後不再出現；還沒做的那幾次一起收掉，已完成、已略過的留作紀錄。"""
        series = await self._visible_series(store_id, viewer, series_id)
        now = utc_now()
        series.archived_at = now
        for row in await self._repo.open_rows_of_series(series.id):
            row.archived_at = now
        await self._session.flush()

    async def set_occurrence_status(
        self,
        store_id: int,
        viewer: Viewer,
        series_id: int,
        occurrence_on: date,
        status: AgendaTaskStatus,
    ) -> AgendaTaskRead:
        """完成／略過重複待辦的某一次（還沒建列就建）。"""
        series = await self._visible_series(store_id, viewer, series_id)
        row = await self._occurrence_row(series, occurrence_on, viewer=viewer)
        self._apply_status(row, status, viewer.user_id)
        await self._session.flush()
        return await self._task_read(store_id, row)

    async def update_occurrence(
        self,
        store_id: int,
        viewer: Viewer,
        series_id: int,
        occurrence_on: date,
        changes: AgendaOccurrenceUpdate,
    ) -> AgendaTaskRead:
        """只改這一次（改期、改時間、備註、負責人），不影響其他次。"""
        series = await self._visible_series(store_id, viewer, series_id)
        row = await self._occurrence_row(series, occurrence_on, viewer=viewer)
        fields = changes.model_fields_set
        if "assignee_staff_id" in fields and changes.assignee_staff_id != row.assignee_staff_id:
            await self._check_assignee(store_id, changes.assignee_staff_id)
        for name in _OCCURRENCE_FIELDS:
            if name in fields:
                setattr(row, name, getattr(changes, name))
        await self._session.flush()
        return await self._task_read(store_id, row)

    async def skip_overdue(
        self, store_id: int, viewer: Viewer, series_id: int, *, today: date
    ) -> int:
        """把這個重複待辦今天以前沒做的都標成略過（好幾週沒打勾時一鍵清掉），回傳幾筆。"""
        series = await self._visible_series(store_id, viewer, series_id)
        rule = _series_rule(series)
        taken = (await self._repo.materialized_dates([series.id])).get(series.id, set())
        new_rows = [
            self._new_occurrence(series, day, actor_user_id=viewer.user_id)
            for day in occurrences_between(rule, rule.starts_on, today - _DAY)
            if day not in taken
        ]
        for row in new_rows:
            self._apply_status(row, AgendaTaskStatus.SKIPPED, viewer.user_id)
        # 用了好幾年沒打勾可能有上千筆：一次寫入，不要一筆一筆來回。
        await self._repo.add_all(new_rows)
        skipped = len(new_rows)
        for row in await self._repo.open_rows_of_series(series.id):
            if not viewer.can_see(row.manager_only):
                continue
            if row.due_on is not None and row.due_on < today:
                self._apply_status(row, AgendaTaskStatus.SKIPPED, viewer.user_id)
                skipped += 1
        await self._session.flush()
        return skipped

    # ── 事件 ──────────────────────────────────────────────

    async def create_event(
        self, store_id: int, viewer: Viewer, data: AgendaEventCreate
    ) -> AgendaEventRead:
        self._guard_manager_only(viewer, data.manager_only)
        ends_on = data.ends_on if data.ends_on is not None else data.starts_on
        rule = _rule(data.recurrence, data.starts_on) if data.recurrence is not None else None
        event = AgendaEvent(
            store_id=store_id,
            kind=data.kind,
            title=data.title,
            note=data.note,
            starts_on=data.starts_on,
            ends_on=ends_on,
            start_time=data.start_time,
            end_time=data.end_time,
            manager_only=data.manager_only,
            repeat_until=rule.ends_on if rule is not None else None,
            created_by=viewer.user_id,
        )
        if rule is not None:
            _store_rule(event, rule)
        self._check_event_times(event)
        await self._repo.add(event)
        return self._event_read(event)

    async def get_event(self, store_id: int, viewer: Viewer, event_id: int) -> AgendaEventRead:
        return self._event_read(await self._visible_event(store_id, viewer, event_id))

    async def list_events(self, store_id: int, viewer: Viewer) -> list[AgendaEventRead]:
        """所有事件（管理用：例如每週公休的設定）。"""
        events = await self._repo.active_events(store_id)
        return [self._event_read(e) for e in events if viewer.can_see(e.manager_only)]

    async def update_event(
        self, store_id: int, viewer: Viewer, event_id: int, changes: AgendaEventUpdate
    ) -> AgendaEventRead:
        """改事件；只改開始日時保留原本的天數。重複規則明確送 null＝改成不重複。"""
        event = await self._visible_event(store_id, viewer, event_id)
        fields = changes.model_fields_set
        if "manager_only" in fields:
            self._guard_manager_only(viewer, bool(changes.manager_only))
            event.manager_only = bool(changes.manager_only)
        length = event.ends_on - event.starts_on
        for name in _EVENT_FIELDS:
            if name in fields:
                setattr(event, name, getattr(changes, name))
        if "starts_on" in fields and "ends_on" not in fields:
            event.ends_on = event.starts_on + length
        if event.ends_on < event.starts_on:
            raise AgendaItemInvalid("結束日不能早於開始日")
        # schema 只在開始、結束日都有送時檢查天數；只改其中一個要在合併後再檢查一次，
        # 否則會撞到資料庫約束變成系統錯誤（Codex 1A 第二輪 P2）。
        if event.ends_on - event.starts_on > timedelta(days=MAX_EVENT_DAYS):
            raise AgendaItemInvalid(f"一個事件最多 {MAX_EVENT_DAYS} 天")
        current = _event_rule(event)
        if "recurrence" in fields:
            fresh = changes.recurrence
        else:
            fresh = _fields_of(current) if current is not None else None
        if fresh is None:
            _clear_rule(event)
        else:
            rule = _rule(fresh, event.starts_on)
            _store_rule(event, rule)
            event.repeat_until = rule.ends_on
        self._check_event_times(event)
        await self._session.flush()
        return self._event_read(event)

    async def archive_event(self, store_id: int, viewer: Viewer, event_id: int) -> None:
        event = await self._visible_event(store_id, viewer, event_id)
        event.archived_at = utc_now()
        await self._session.flush()

    async def set_event_occurrence_cancelled(
        self, store_id: int, viewer: Viewer, event_id: int, on: date, *, cancelled: bool
    ) -> AgendaEventRead:
        """取消或恢復重複事件的某一次（例：這個週一照常營業）。不重複的事件直接刪除。

        先鎖住這筆事件再讀取消清單：兩台同時取消不同的日子時，不鎖的話後提交的會把先提交的
        蓋掉（Codex 1A 第四輪 P2）。
        """
        event = await self._visible_event(store_id, viewer, event_id, for_update=True)
        rule = _event_rule(event)
        if rule is None:
            raise AgendaItemInvalid("不重複的事件請直接刪除")
        if not is_occurrence(rule, on):
            raise AgendaItemInvalid(f"{on.isoformat()} 不是這個事件的其中一次")
        dates = set(event.cancelled_dates)
        if cancelled:
            dates.add(on)
        else:
            dates.discard(on)
        event.cancelled_dates = sorted(dates)  # 換新的 list，ORM 才看得出陣列有變
        await self._session.flush()
        return self._event_read(event)

    # ── 彙整與今天 ────────────────────────────────────────

    async def feed(
        self, store_id: int, viewer: Viewer, start: date, end: date, *, today: date
    ) -> list[AgendaItemRead]:
        """行事曆上 [start, end]（含兩端）的每一列，依日期、時間排序。"""
        if end < start:
            raise AgendaItemInvalid("結束日不能早於開始日")
        if (end - start).days + 1 > MAX_FEED_DAYS:
            raise AgendaItemInvalid(f"一次最多查 {MAX_FEED_DAYS} 天")
        items = await self._task_items(store_id, viewer, start, end, today=today)
        items += await self._event_items(store_id, viewer, start, end)
        items += await self._campaign_items(store_id, viewer, start, end)
        return sorted(items, key=_sort_key)

    async def today_summary(self, store_id: int, viewer: Viewer, *, today: date) -> AgendaTodayRead:
        """首頁與開店前檢查的「今天」（docs/99 §8）。"""
        rows = [
            r
            for r in await self._repo.tasks_due_between(
                store_id, today, today + timedelta(days=MAX_LEAD_DAYS)
            )
            if viewer.can_see(r.manager_only)
        ]
        overdue_rows = [
            r
            for r in await self._repo.open_tasks_due_before(store_id, today)
            if viewer.can_see(r.manager_only)
        ]
        series_list = await self._visible_active_series(store_id, viewer)
        taken = await self._repo.materialized_dates([s.id for s in series_list])
        names = await self._names(store_id, [*rows, *overdue_rows], series_list)

        due_today = [self._row_item(r, names, today) for r in rows if r.due_on == today]
        upcoming = [
            self._row_item(r, names, today)
            for r in rows
            if r.status is AgendaTaskStatus.OPEN
            and r.due_on is not None
            and today < r.due_on <= today + timedelta(days=r.lead_days)
        ]
        overdue = [self._row_item(r, names, today) for r in overdue_rows]
        for series in series_list:
            rule = _series_rule(series)
            done = taken.get(series.id, set())
            lead_end = today + timedelta(days=series.lead_days)
            for bucket, low, high in (
                (due_today, today, today),
                (upcoming, today + _DAY, lead_end),
                (overdue, rule.starts_on, today - _DAY),
            ):
                bucket.extend(
                    self._occurrence_item(series, day, names, today)
                    for day in occurrences_between(rule, low, high)
                    if day not in done
                )

        pending = await ConsignmentService(self._session).count_settlements(
            store_id, status=ConsignmentSettlementStatus.PENDING
        )
        campaigns = await self._campaign_items(store_id, viewer, today, today)
        due_today.sort(key=_sort_key)
        overdue.sort(key=_sort_key)
        upcoming.sort(key=_sort_key)
        return AgendaTodayRead(
            today=today,
            due_today=due_today,
            overdue=overdue,
            upcoming=upcoming,
            events=sorted(await self._event_items(store_id, viewer, today, today), key=_sort_key),
            campaigns=campaigns,
            consignment_pending_count=pending,
            badge_count=sum(1 for i in due_today if i.status is AgendaTaskStatus.OPEN)
            + len(overdue),
        )

    # ── 內部：可見性與驗證 ────────────────────────────────

    @staticmethod
    def _guard_manager_only(viewer: Viewer, manager_only: bool) -> None:
        if manager_only and not viewer.is_manager:
            raise AgendaForbidden("只限管理者的項目要由管理者建立")

    async def _check_assignee(self, store_id: int, staff_id: int | None) -> None:
        if staff_id is None:
            return
        if await self._staff.get_active_member(store_id, staff_id) is None:
            raise AgendaItemInvalid("負責人要是本店在職的員工")

    @staticmethod
    def _check_event_times(event: AgendaEvent) -> None:
        if (
            event.starts_on == event.ends_on
            and event.start_time is not None
            and event.end_time is not None
            and event.end_time < event.start_time
        ):
            raise AgendaItemInvalid("結束時間不能早於開始時間")

    async def _visible_task(self, store_id: int, viewer: Viewer, task_id: int) -> AgendaTask:
        task = await self._repo.get_task(store_id, task_id)
        if task is None or not viewer.can_see(task.manager_only):
            raise AgendaItemNotFound(f"找不到待辦 {task_id}")
        return task

    async def _visible_series(
        self, store_id: int, viewer: Viewer, series_id: int
    ) -> AgendaTaskSeries:
        series = await self._repo.get_series(store_id, series_id)
        if series is None or not viewer.can_see(series.manager_only):
            raise AgendaItemNotFound(f"找不到重複待辦 {series_id}")
        return series

    async def _visible_event(
        self, store_id: int, viewer: Viewer, event_id: int, *, for_update: bool = False
    ) -> AgendaEvent:
        event = await self._repo.get_event(store_id, event_id, for_update=for_update)
        if event is None or not viewer.can_see(event.manager_only):
            raise AgendaItemNotFound(f"找不到事件 {event_id}")
        return event

    async def _visible_active_series(self, store_id: int, viewer: Viewer) -> list[AgendaTaskSeries]:
        return [
            s for s in await self._repo.active_series(store_id) if viewer.can_see(s.manager_only)
        ]

    # ── 內部：重複待辦的某一次 ────────────────────────────

    @staticmethod
    def _apply_status(task: AgendaTask, status: AgendaTaskStatus, user_id: int) -> None:
        task.status = status
        if status is AgendaTaskStatus.OPEN:
            task.resolved_at = None
            task.resolved_by = None
        else:
            task.resolved_at = utc_now()
            task.resolved_by = user_id

    @staticmethod
    def _new_occurrence(series: AgendaTaskSeries, day: date, *, actor_user_id: int) -> AgendaTask:
        return AgendaTask(
            store_id=series.store_id,
            title=series.title,
            note=series.note,
            due_on=day,
            due_time=series.due_time,
            assignee_staff_id=series.assignee_staff_id,
            manager_only=series.manager_only,
            link_href=series.link_href,
            lead_days=series.lead_days,
            series_id=series.id,
            occurrence_on=day,
            status=AgendaTaskStatus.OPEN,
            created_by=actor_user_id,
        )

    async def _occurrence_row(
        self, series: AgendaTaskSeries, day: date, *, viewer: Viewer
    ) -> AgendaTask:
        """某一次的那一列；還沒建就建（要是規則上真的有這一次）。

        那一列本身也要這個人看得到：規則看得到不代表每一次都看得到（Codex 1A 第一輪 P1）。
        """
        existing = await self._repo.get_occurrence(series.id, day)
        if existing is not None:
            if existing.archived_at is not None or not viewer.can_see(existing.manager_only):
                raise AgendaItemNotFound("找不到這一次")
            return existing
        if not is_occurrence(_series_rule(series), day):
            raise AgendaItemInvalid(f"{day.isoformat()} 不是這個重複待辦的其中一次")
        row = self._new_occurrence(series, day, actor_user_id=viewer.user_id)
        try:
            async with self._session.begin_nested():
                await self._repo.add(row)
        except IntegrityError as exc:  # 兩台同時打勾同一次：唯一鍵兜底，改用對方建好的那列
            raced = await self._repo.get_occurrence(series.id, day)
            if raced is None:
                raise AgendaItemInvalid("這一次正在被別人處理，請重試") from exc
            return raced
        return row

    async def _keep_missed(
        self, series: AgendaTaskSeries, rule: Recurrence, *, today: date, actor_user_id: int
    ) -> None:
        """改規則前：舊規則下今天以前沒建列的那幾次，先建成未完成的列，才不會一改就消失。"""
        taken = (await self._repo.materialized_dates([series.id])).get(series.id, set())
        await self._repo.add_all(
            [
                self._new_occurrence(series, day, actor_user_id=actor_user_id)
                for day in occurrences_between(rule, rule.starts_on, today - _DAY)
                if day not in taken
            ]
        )

    # ── 內部：組回應 ──────────────────────────────────────

    async def _names(
        self, store_id: int, rows: list[AgendaTask], series: list[AgendaTaskSeries]
    ) -> dict[int, str]:
        ids = {r.assignee_staff_id for r in rows if r.assignee_staff_id is not None}
        ids |= {s.assignee_staff_id for s in series if s.assignee_staff_id is not None}
        return await self._staff.names_for(store_id, ids)

    async def _task_read(self, store_id: int, task: AgendaTask) -> AgendaTaskRead:
        return (await self._task_reads(store_id, [task]))[0]

    async def _task_reads(self, store_id: int, tasks: list[AgendaTask]) -> list[AgendaTaskRead]:
        names = await self._names(store_id, tasks, [])
        resolvers = sorted({t.resolved_by for t in tasks if t.resolved_by is not None})
        usernames = await UserService(self._session).usernames_for(store_id, resolvers)
        return [
            AgendaTaskRead(
                id=t.id,
                title=t.title,
                note=t.note,
                due_on=t.due_on,
                due_time=t.due_time,
                assignee_staff_id=t.assignee_staff_id,
                assignee_name=names.get(t.assignee_staff_id) if t.assignee_staff_id else None,
                manager_only=t.manager_only,
                link_href=t.link_href,
                lead_days=t.lead_days,
                series_id=t.series_id,
                occurrence_on=t.occurrence_on,
                status=t.status,
                resolved_at=t.resolved_at,
                resolved_by_name=usernames.get(t.resolved_by) if t.resolved_by else None,
                created_at=t.created_at,
            )
            for t in tasks
        ]

    async def _series_read(
        self, store_id: int, series: AgendaTaskSeries, *, today: date | None
    ) -> AgendaSeriesRead:
        rule = _series_rule(series)
        start = today if today is not None else store_date(utc_now())
        upcoming = occurrences_between(rule, start, start + _NEXT_LOOKAHEAD, limit=1)
        names = await self._names(store_id, [], [series])
        return AgendaSeriesRead(
            id=series.id,
            title=series.title,
            note=series.note,
            due_time=series.due_time,
            assignee_staff_id=series.assignee_staff_id,
            assignee_name=names.get(series.assignee_staff_id) if series.assignee_staff_id else None,
            manager_only=series.manager_only,
            link_href=series.link_href,
            lead_days=series.lead_days,
            starts_on=series.starts_on,
            recurrence=_fields_of(rule),
            next_on=upcoming[0] if upcoming else None,
        )

    @staticmethod
    def _event_read(event: AgendaEvent) -> AgendaEventRead:
        rule = _event_rule(event)
        return AgendaEventRead(
            id=event.id,
            kind=event.kind,
            title=event.title,
            note=event.note,
            starts_on=event.starts_on,
            ends_on=event.ends_on,
            start_time=event.start_time,
            end_time=event.end_time,
            manager_only=event.manager_only,
            recurrence=_fields_of(rule) if rule is not None else None,
            cancelled_dates=list(event.cancelled_dates),
        )

    @staticmethod
    def _row_item(task: AgendaTask, names: dict[int, str], today: date) -> AgendaItemRead:
        day = _due(task)
        return AgendaItemRead(
            key=f"task:{task.id}",
            kind=AgendaItemKind.TASK,
            day=day,
            start_time=task.due_time,
            title=task.title,
            note=task.note,
            status=task.status,
            overdue=task.status is AgendaTaskStatus.OPEN and day < today,
            manager_only=task.manager_only,
            assignee_staff_id=task.assignee_staff_id,
            assignee_name=names.get(task.assignee_staff_id) if task.assignee_staff_id else None,
            lead_days=task.lead_days,
            task_id=task.id,
            series_id=task.series_id,
            occurrence_on=task.occurrence_on,
            href=task.link_href,
        )

    @staticmethod
    def _occurrence_item(
        series: AgendaTaskSeries, day: date, names: dict[int, str], today: date
    ) -> AgendaItemRead:
        return AgendaItemRead(
            key=f"occurrence:{series.id}:{day.isoformat()}",
            kind=AgendaItemKind.TASK,
            day=day,
            start_time=series.due_time,
            title=series.title,
            note=series.note,
            status=AgendaTaskStatus.OPEN,
            overdue=day < today,
            manager_only=series.manager_only,
            assignee_staff_id=series.assignee_staff_id,
            assignee_name=(
                names.get(series.assignee_staff_id) if series.assignee_staff_id else None
            ),
            lead_days=series.lead_days,
            series_id=series.id,
            occurrence_on=day,
            href=series.link_href,
        )

    async def _task_items(
        self, store_id: int, viewer: Viewer, start: date, end: date, *, today: date
    ) -> list[AgendaItemRead]:
        rows = [
            r
            for r in await self._repo.tasks_due_between(store_id, start, end)
            if viewer.can_see(r.manager_only)
        ]
        series_list = await self._visible_active_series(store_id, viewer)
        taken = await self._repo.materialized_dates([s.id for s in series_list])
        names = await self._names(store_id, rows, series_list)
        items = [self._row_item(r, names, today) for r in rows]
        for series in series_list:
            done = taken.get(series.id, set())
            items.extend(
                self._occurrence_item(series, day, names, today)
                for day in occurrences_between(_series_rule(series), start, end)
                if day not in done
            )
        return items

    async def _event_items(
        self, store_id: int, viewer: Viewer, start: date, end: date
    ) -> list[AgendaItemRead]:
        items: list[AgendaItemRead] = []
        for event in await self._repo.active_events(store_id):
            if not viewer.can_see(event.manager_only):
                continue
            length = event.ends_on - event.starts_on
            rule = _event_rule(event)
            if rule is None:
                starts = (
                    [event.starts_on] if event.starts_on <= end and event.ends_on >= start else []
                )
            else:
                cancelled = set(event.cancelled_dates)
                starts = [
                    d for d in occurrences_between(rule, start - length, end) if d not in cancelled
                ]
            items.extend(
                AgendaItemRead(
                    key=f"event:{event.id}:{day.isoformat()}",
                    kind=AgendaItemKind.EVENT,
                    day=day,
                    end_day=day + length if length else None,
                    start_time=event.start_time,
                    end_time=event.end_time,
                    title=event.title,
                    note=event.note,
                    manager_only=event.manager_only,
                    event_id=event.id,
                    event_kind=event.kind,
                )
                for day in starts
            )
        return items

    async def _campaign_items(
        self, store_id: int, viewer: Viewer, start: date, end: date
    ) -> list[AgendaItemRead]:
        statuses = _MANAGER_CAMPAIGN_STATUSES if viewer.is_manager else _CLERK_CAMPAIGN_STATUSES
        campaigns = await CampaignService(self._session).list_overlapping(
            store_id, store_day_bounds(start)[0], store_day_bounds(end)[1], statuses=statuses
        )
        href = "/campaigns" if viewer.is_manager else None  # 活動頁只有管理者進得去
        items: list[AgendaItemRead] = []
        for campaign in campaigns:
            first = store_date(campaign.starts_at)
            last = store_period_end_day(campaign.ends_at)
            marks = [(AgendaItemKind.CAMPAIGN_START, first)]
            if last != first:
                marks.append((AgendaItemKind.CAMPAIGN_END, last))
            items.extend(
                AgendaItemRead(
                    key=f"{kind.value.lower()}:{campaign.id}",
                    kind=kind,
                    day=day,
                    title=campaign.name,
                    campaign_id=campaign.id,
                    campaign_status=campaign.status,
                    href=href,
                )
                for kind, day in marks
                if start <= day <= end
            )
        return items
