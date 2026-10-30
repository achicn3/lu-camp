"""行事曆服務層（docs/99 §3、§4、§8；ADR-034 決定 6）。

「今天」一律明確傳入（2026-11-10，週二），不依賴 now()——跨日、跨月的邊界才測得準。
"""

from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import store_day_bounds
from app.modules.agenda.models import AgendaTask
from app.modules.agenda.schemas import (
    AgendaEventCreate,
    AgendaEventUpdate,
    AgendaOccurrenceUpdate,
    AgendaSeriesCreate,
    AgendaSeriesUpdate,
    AgendaTaskCreate,
    AgendaTaskUpdate,
    RecurrenceFields,
)
from app.modules.agenda.service import AgendaService, Viewer
from app.modules.campaigns.models import Campaign
from app.modules.staff.schemas import StaffMemberUpdate
from app.modules.staff.service import StaffService
from app.shared.enums import (
    AgendaEventKind,
    AgendaItemKind,
    AgendaTaskStatus,
    CampaignKind,
    CampaignStatus,
    RecurrenceKind,
)
from app.shared.exceptions import AgendaForbidden, AgendaItemInvalid, AgendaItemNotFound
from tests.integration.agenda_helpers import SeededStore, seed_store

TODAY = date(2026, 11, 10)  # 週二
NOV_1 = date(2026, 11, 1)
NOV_30 = date(2026, 11, 30)


def _viewers(shop: SeededStore) -> tuple[Viewer, Viewer]:
    return (
        Viewer(user_id=shop.manager_id, is_manager=True),
        Viewer(user_id=shop.clerk_id, is_manager=False),
    )


def _weekly(*weekdays: int) -> RecurrenceFields:
    return RecurrenceFields(kind=RecurrenceKind.WEEKLY, weekdays=list(weekdays))


def _monthly(day: int) -> RecurrenceFields:
    return RecurrenceFields(kind=RecurrenceKind.MONTHLY, day_of_month=day)


async def test_one_off_tasks_split_into_overdue_today_and_upcoming(
    db_session: AsyncSession,
) -> None:
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)

    async def task(title: str, due_on: date | None, lead_days: int = 3) -> int:
        created = await svc.create_task(
            shop.store_id, mgr, AgendaTaskCreate(title=title, due_on=due_on, lead_days=lead_days)
        )
        return created.id

    await task("過期", date(2026, 11, 5))
    await task("今天", TODAY)
    await task("快到期", date(2026, 11, 12))
    await task("還早", date(2026, 11, 20))
    done_id = await task("做完了", date(2026, 11, 4))
    await svc.set_task_status(shop.store_id, mgr, done_id, AgendaTaskStatus.DONE)
    await task("沒有日期", None)

    today = await svc.today_summary(shop.store_id, mgr, today=TODAY)
    assert [i.title for i in today.overdue] == ["過期"]
    assert [i.title for i in today.due_today] == ["今天"]
    assert [i.title for i in today.upcoming] == ["快到期"]
    assert today.badge_count == 2

    feed = await svc.feed(shop.store_id, mgr, NOV_1, NOV_30, today=TODAY)
    assert [(i.title, i.day) for i in feed] == [
        ("做完了", date(2026, 11, 4)),
        ("過期", date(2026, 11, 5)),
        ("今天", TODAY),
        ("快到期", date(2026, 11, 12)),
        ("還早", date(2026, 11, 20)),
    ]
    by_title = {i.title: i for i in feed}
    assert by_title["過期"].overdue is True
    assert by_title["做完了"].status is AgendaTaskStatus.DONE
    assert by_title["做完了"].overdue is False
    assert all(i.kind is AgendaItemKind.TASK for i in feed)

    undated = await svc.list_undated(shop.store_id, mgr)
    assert [t.title for t in undated] == ["沒有日期"]


async def test_weekly_series_overdue_done_and_skip_overdue(db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    mgr, clerk = _viewers(shop)
    svc = AgendaService(db_session)
    series = await svc.create_series(
        shop.store_id,
        mgr,
        AgendaSeriesCreate(title="叫牛奶", starts_on=NOV_1, lead_days=0, recurrence=_weekly(0, 3)),
    )

    feed = await svc.feed(shop.store_id, mgr, NOV_1, date(2026, 11, 14), today=TODAY)
    assert [(i.key, i.overdue) for i in feed] == [
        (f"occurrence:{series.id}:2026-11-02", True),
        (f"occurrence:{series.id}:2026-11-05", True),
        (f"occurrence:{series.id}:2026-11-09", True),
        (f"occurrence:{series.id}:2026-11-12", False),
    ]
    assert (await svc.today_summary(shop.store_id, mgr, today=TODAY)).badge_count == 3

    # 店員打勾 11/5 那一次：建出一列，記是誰做的
    done = await svc.set_occurrence_status(
        shop.store_id, clerk, series.id, date(2026, 11, 5), AgendaTaskStatus.DONE
    )
    assert done.status is AgendaTaskStatus.DONE
    assert done.occurrence_on == date(2026, 11, 5)
    assert done.resolved_by_name == "門市-clk"
    overdue = (await svc.today_summary(shop.store_id, mgr, today=TODAY)).overdue
    assert [i.day for i in overdue] == [date(2026, 11, 2), date(2026, 11, 9)]

    feed = await svc.feed(shop.store_id, mgr, NOV_1, date(2026, 11, 14), today=TODAY)
    assert feed[1].key == f"task:{done.id}"
    assert feed[1].status is AgendaTaskStatus.DONE

    # 好幾次沒打勾：一鍵把過期的都略過，下一次照常出現
    assert await svc.skip_overdue(shop.store_id, mgr, series.id, today=TODAY) == 2
    today = await svc.today_summary(shop.store_id, mgr, today=TODAY)
    assert today.overdue == []
    statuses = {
        i.day: i.status
        for i in await svc.feed(shop.store_id, mgr, NOV_1, date(2026, 11, 14), today=TODAY)
    }
    assert statuses == {
        date(2026, 11, 2): AgendaTaskStatus.SKIPPED,
        date(2026, 11, 5): AgendaTaskStatus.DONE,
        date(2026, 11, 9): AgendaTaskStatus.SKIPPED,
        date(2026, 11, 12): AgendaTaskStatus.OPEN,
    }


async def test_editing_one_occurrence_moves_only_that_one(db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    series = await svc.create_series(
        shop.store_id,
        mgr,
        AgendaSeriesCreate(title="叫牛奶", starts_on=NOV_1, recurrence=_weekly(3)),
    )

    moved = await svc.update_occurrence(
        shop.store_id,
        mgr,
        series.id,
        date(2026, 11, 12),
        AgendaOccurrenceUpdate(due_on=date(2026, 11, 14), due_time=time(15, 0)),
    )
    assert moved.due_on == date(2026, 11, 14)
    feed = await svc.feed(shop.store_id, mgr, NOV_1, NOV_30, today=TODAY)
    assert [(i.day, i.occurrence_on) for i in feed] == [
        (date(2026, 11, 5), date(2026, 11, 5)),
        (date(2026, 11, 14), date(2026, 11, 12)),
        (date(2026, 11, 19), date(2026, 11, 19)),
        (date(2026, 11, 26), date(2026, 11, 26)),
    ]
    assert feed[1].start_time == time(15, 0)

    with pytest.raises(AgendaItemInvalid):  # 11/11 是週三，不是這個規則的其中一次
        await svc.set_occurrence_status(
            shop.store_id, mgr, series.id, date(2026, 11, 11), AgendaTaskStatus.DONE
        )


async def test_rule_change_keeps_missed_occurrences_and_restarts_today(
    db_session: AsyncSession,
) -> None:
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    series = await svc.create_series(
        shop.store_id,
        mgr,
        AgendaSeriesCreate(title="付房租", starts_on=date(2026, 9, 1), recurrence=_monthly(5)),
    )
    await svc.set_occurrence_status(
        shop.store_id, mgr, series.id, date(2026, 10, 5), AgendaTaskStatus.DONE
    )

    updated = await svc.update_series(
        shop.store_id, mgr, series.id, AgendaSeriesUpdate(recurrence=_monthly(20)), today=TODAY
    )
    assert updated.starts_on == TODAY
    assert updated.next_on == date(2026, 11, 20)

    feed = await svc.feed(shop.store_id, mgr, date(2026, 9, 1), NOV_30, today=TODAY)
    assert [(i.day, i.status, i.overdue) for i in feed] == [
        (date(2026, 9, 5), AgendaTaskStatus.OPEN, True),  # 改規則前沒付的，照舊提醒
        (date(2026, 10, 5), AgendaTaskStatus.DONE, False),
        (date(2026, 11, 5), AgendaTaskStatus.OPEN, True),
        (date(2026, 11, 20), AgendaTaskStatus.OPEN, False),  # 新規則從今天起算
    ]


async def test_renaming_a_series_does_not_restart_it(db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    series = await svc.create_series(
        shop.store_id,
        mgr,
        AgendaSeriesCreate(title="付房租", starts_on=date(2026, 9, 1), recurrence=_monthly(5)),
    )
    renamed = await svc.update_series(
        shop.store_id, mgr, series.id, AgendaSeriesUpdate(title="付店面房租"), today=TODAY
    )
    assert renamed.starts_on == date(2026, 9, 1)
    overdue = (await svc.today_summary(shop.store_id, mgr, today=TODAY)).overdue
    assert [(i.title, i.day) for i in overdue] == [
        ("付店面房租", date(2026, 9, 5)),
        ("付店面房租", date(2026, 10, 5)),
        ("付店面房租", date(2026, 11, 5)),
    ]


async def test_deleting_a_series_keeps_history_only(db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    series = await svc.create_series(
        shop.store_id,
        mgr,
        AgendaSeriesCreate(title="叫牛奶", starts_on=NOV_1, recurrence=_weekly(0)),
    )
    await svc.set_occurrence_status(
        shop.store_id, mgr, series.id, date(2026, 11, 2), AgendaTaskStatus.DONE
    )
    await svc.update_occurrence(
        shop.store_id,
        mgr,
        series.id,
        date(2026, 11, 9),
        AgendaOccurrenceUpdate(note="改叫豆漿"),
    )

    await svc.archive_series(shop.store_id, mgr, series.id)

    feed = await svc.feed(shop.store_id, mgr, NOV_1, NOV_30, today=TODAY)
    assert [(i.day, i.status) for i in feed] == [(date(2026, 11, 2), AgendaTaskStatus.DONE)]
    assert (await svc.today_summary(shop.store_id, mgr, today=TODAY)).overdue == []
    with pytest.raises(AgendaItemNotFound):
        await svc.get_series(shop.store_id, mgr, series.id)


async def test_events_recurring_cancelled_and_multi_day(db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    closed = await svc.create_event(
        shop.store_id,
        mgr,
        AgendaEventCreate(
            kind=AgendaEventKind.CLOSED, title="公休", starts_on=NOV_1, recurrence=_weekly(0)
        ),
    )
    await svc.set_event_occurrence_cancelled(
        shop.store_id, mgr, closed.id, date(2026, 11, 16), cancelled=True
    )
    await svc.create_event(
        shop.store_id,
        mgr,
        AgendaEventCreate(
            kind=AgendaEventKind.MARKET,
            title="露營市集",
            starts_on=date(2026, 11, 7),
            ends_on=date(2026, 11, 8),
        ),
    )

    feed = await svc.feed(shop.store_id, mgr, NOV_1, NOV_30, today=TODAY)
    assert [(i.title, i.day, i.end_day) for i in feed] == [
        ("公休", date(2026, 11, 2), None),
        ("露營市集", date(2026, 11, 7), date(2026, 11, 8)),
        ("公休", date(2026, 11, 9), None),
        ("公休", date(2026, 11, 23), None),
        ("公休", date(2026, 11, 30), None),
    ]
    assert all(i.kind is AgendaItemKind.EVENT for i in feed)

    # 跨天的事件：查第二天也要看得到
    second_day = await svc.feed(
        shop.store_id, mgr, date(2026, 11, 8), date(2026, 11, 8), today=TODAY
    )
    assert [i.title for i in second_day] == ["露營市集"]

    # 恢復被取消的那一次
    await svc.set_event_occurrence_cancelled(
        shop.store_id, mgr, closed.id, date(2026, 11, 16), cancelled=False
    )
    days = [
        i.day
        for i in await svc.feed(shop.store_id, mgr, NOV_1, NOV_30, today=TODAY)
        if i.title == "公休"
    ]
    assert date(2026, 11, 16) in days

    with pytest.raises(AgendaItemInvalid):  # 11/17 不是公休日
        await svc.set_event_occurrence_cancelled(
            shop.store_id, mgr, closed.id, date(2026, 11, 17), cancelled=True
        )


async def test_recurring_multi_day_event_shows_when_window_starts_mid_event(
    db_session: AsyncSession,
) -> None:
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    await svc.create_event(
        shop.store_id,
        mgr,
        AgendaEventCreate(
            kind=AgendaEventKind.MARKET,
            title="月初市集",
            starts_on=NOV_1,
            ends_on=date(2026, 11, 2),
            recurrence=_monthly(1),
        ),
    )
    feed = await svc.feed(shop.store_id, mgr, date(2026, 12, 2), date(2026, 12, 2), today=TODAY)
    assert [(i.day, i.end_day) for i in feed] == [(date(2026, 12, 1), date(2026, 12, 2))]


async def test_event_can_stop_repeating(db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    event = await svc.create_event(
        shop.store_id,
        mgr,
        AgendaEventCreate(
            kind=AgendaEventKind.CLOSED, title="公休", starts_on=NOV_1, recurrence=_weekly(0)
        ),
    )
    updated = await svc.update_event(
        shop.store_id,
        mgr,
        event.id,
        AgendaEventUpdate(recurrence=None, starts_on=date(2026, 11, 2)),
    )
    assert updated.recurrence is None
    feed = await svc.feed(shop.store_id, mgr, NOV_1, NOV_30, today=TODAY)
    assert [i.day for i in feed] == [date(2026, 11, 2)]


async def test_manager_only_items_are_invisible_to_clerks(db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    mgr, clerk = _viewers(shop)
    svc = AgendaService(db_session)
    secret = await svc.create_task(
        shop.store_id,
        mgr,
        AgendaTaskCreate(title="跟房東談續約", due_on=TODAY, manager_only=True),
    )
    await svc.create_event(
        shop.store_id,
        mgr,
        AgendaEventCreate(
            kind=AgendaEventKind.OTHER, title="見會計師", starts_on=TODAY, manager_only=True
        ),
    )
    shared = await svc.create_task(
        shop.store_id, clerk, AgendaTaskCreate(title="補發票紙", due_on=TODAY)
    )

    clerk_feed = await svc.feed(shop.store_id, clerk, NOV_1, NOV_30, today=TODAY)
    assert [i.title for i in clerk_feed] == ["補發票紙"]
    clerk_today = await svc.today_summary(shop.store_id, clerk, today=TODAY)
    assert [i.title for i in clerk_today.due_today] == ["補發票紙"]
    assert clerk_today.events == []
    mgr_titles = {i.title for i in await svc.feed(shop.store_id, mgr, NOV_1, NOV_30, today=TODAY)}
    assert mgr_titles == {"跟房東談續約", "見會計師", "補發票紙"}

    with pytest.raises(AgendaItemNotFound):
        await svc.get_task(shop.store_id, clerk, secret.id)
    with pytest.raises(AgendaForbidden):
        await svc.create_task(
            shop.store_id, clerk, AgendaTaskCreate(title="偷藏", due_on=TODAY, manager_only=True)
        )
    with pytest.raises(AgendaForbidden):
        await svc.update_task(shop.store_id, clerk, shared.id, AgendaTaskUpdate(manager_only=True))


async def test_campaign_start_and_last_day_appear(db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    mgr, clerk = _viewers(shop)
    svc = AgendaService(db_session)
    starts_at = store_day_bounds(date(2026, 11, 11))[0]
    ends_at = store_day_bounds(date(2026, 11, 14))[0]  # [11/11, 11/14) → 最後一天 11/13

    def campaign(name: str, status: CampaignStatus) -> Campaign:
        return Campaign(
            store_id=shop.store_id,
            name=name,
            kind=CampaignKind.PERCENT_OFF,
            discount_pct=10,
            starts_at=starts_at,
            ends_at=ends_at,
            status=status,
            created_by=shop.manager_id,
        )

    live = campaign("雙11", CampaignStatus.ACTIVE)
    db_session.add_all([live, campaign("取消掉的", CampaignStatus.CANCELLED)])
    await db_session.flush()

    feed = await svc.feed(shop.store_id, clerk, NOV_1, NOV_30, today=TODAY)
    assert [(i.kind, i.day, i.title) for i in feed] == [
        (AgendaItemKind.CAMPAIGN_START, date(2026, 11, 11), "雙11"),
        (AgendaItemKind.CAMPAIGN_END, date(2026, 11, 13), "雙11"),
    ]
    assert feed[0].campaign_id == live.id
    assert feed[0].href is None  # 活動頁只有管理者進得去
    mgr_feed = await svc.feed(shop.store_id, mgr, NOV_1, NOV_30, today=TODAY)
    assert mgr_feed[0].href == "/campaigns"

    on_the_day = await svc.today_summary(shop.store_id, mgr, today=date(2026, 11, 11))
    assert [i.kind for i in on_the_day.campaigns] == [AgendaItemKind.CAMPAIGN_START]


async def test_assignee_must_be_an_active_member_of_this_store(db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    other = await seed_store(db_session, name="二店")
    mgr, _ = _viewers(shop)
    staff = StaffService(db_session)
    wang = await staff.create_member(shop.store_id, name="小王", actor_user_id=shop.manager_id)
    retired = await staff.create_member(shop.store_id, name="老李", actor_user_id=shop.manager_id)
    await staff.update_member(
        shop.store_id, retired.id, StaffMemberUpdate(is_active=False), actor_user_id=shop.manager_id
    )
    foreign = await staff.create_member(other.store_id, name="外人", actor_user_id=other.manager_id)
    svc = AgendaService(db_session)

    created = await svc.create_task(
        shop.store_id, mgr, AgendaTaskCreate(title="盤點", due_on=TODAY, assignee_staff_id=wang.id)
    )
    assert created.assignee_name == "小王"
    feed = await svc.feed(shop.store_id, mgr, TODAY, TODAY, today=TODAY)
    assert feed[0].assignee_name == "小王"

    for bad in (retired.id, foreign.id):
        with pytest.raises(AgendaItemInvalid):
            await svc.create_task(
                shop.store_id,
                mgr,
                AgendaTaskCreate(title="盤點", due_on=TODAY, assignee_staff_id=bad),
            )


async def test_reopen_clears_who_did_it(db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    mgr, clerk = _viewers(shop)
    svc = AgendaService(db_session)
    task = await svc.create_task(shop.store_id, mgr, AgendaTaskCreate(title="補零錢", due_on=TODAY))

    done = await svc.set_task_status(shop.store_id, clerk, task.id, AgendaTaskStatus.DONE)
    assert done.resolved_by_name == "門市-clk"
    assert done.resolved_at is not None and done.resolved_at <= datetime.now(UTC)

    reopened = await svc.set_task_status(shop.store_id, mgr, task.id, AgendaTaskStatus.OPEN)
    assert reopened.status is AgendaTaskStatus.OPEN
    assert reopened.resolved_at is None
    assert reopened.resolved_by_name is None


async def test_occurrence_rows_cannot_be_deleted_or_undated(db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    series = await svc.create_series(
        shop.store_id,
        mgr,
        AgendaSeriesCreate(title="叫牛奶", starts_on=NOV_1, recurrence=_weekly(0)),
    )
    row = await svc.set_occurrence_status(
        shop.store_id, mgr, series.id, date(2026, 11, 2), AgendaTaskStatus.DONE
    )
    with pytest.raises(AgendaItemInvalid):  # 重複待辦的某一次用「略過」
        await svc.archive_task(shop.store_id, mgr, row.id)
    with pytest.raises(AgendaItemInvalid):
        await svc.update_task(shop.store_id, mgr, row.id, AgendaTaskUpdate(due_on=None))


async def test_feed_range_is_bounded(db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    with pytest.raises(AgendaItemInvalid):
        await svc.feed(shop.store_id, mgr, NOV_30, NOV_1, today=TODAY)
    with pytest.raises(AgendaItemInvalid):
        await svc.feed(shop.store_id, mgr, NOV_1, date(2027, 3, 1), today=TODAY)


async def test_skip_overdue_also_skips_rows_kept_from_an_old_rule(
    db_session: AsyncSession,
) -> None:
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    series = await svc.create_series(
        shop.store_id,
        mgr,
        AgendaSeriesCreate(title="付房租", starts_on=date(2026, 9, 1), recurrence=_monthly(5)),
    )
    # 改規則：9/5、10/5、11/5 沒付的先建成未完成的列，新規則從今天起算
    await svc.update_series(
        shop.store_id, mgr, series.id, AgendaSeriesUpdate(recurrence=_monthly(20)), today=TODAY
    )

    assert await svc.skip_overdue(shop.store_id, mgr, series.id, today=TODAY) == 3
    assert (await svc.today_summary(shop.store_id, mgr, today=TODAY)).overdue == []


async def test_clearing_the_date_needs_clearing_the_time_too(db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    task = await svc.create_task(
        shop.store_id,
        mgr,
        AgendaTaskCreate(title="打電話給房東", due_on=TODAY, due_time=time(10, 0)),
    )
    with pytest.raises(AgendaItemInvalid):
        await svc.update_task(shop.store_id, mgr, task.id, AgendaTaskUpdate(due_on=None))

    undated = await svc.update_task(
        shop.store_id, mgr, task.id, AgendaTaskUpdate(due_on=None, due_time=None)
    )
    assert undated.due_on is None
    assert [t.title for t in await svc.list_undated(shop.store_id, mgr)] == ["打電話給房東"]


async def test_series_visibility_change_carries_over_to_existing_rows(
    db_session: AsyncSession,
) -> None:
    """Codex 1A 第一輪 P1：把全店看得到的重複待辦改成只限管理者，已經建列的那幾次也要跟著藏起來。"""
    shop = await seed_store(db_session)
    mgr, clerk = _viewers(shop)
    svc = AgendaService(db_session)
    series = await svc.create_series(
        shop.store_id,
        mgr,
        AgendaSeriesCreate(title="付房租", starts_on=NOV_1, recurrence=_weekly(0)),
    )
    await svc.set_occurrence_status(
        shop.store_id, clerk, series.id, date(2026, 11, 2), AgendaTaskStatus.DONE
    )

    await svc.update_series(
        shop.store_id, mgr, series.id, AgendaSeriesUpdate(manager_only=True), today=TODAY
    )

    assert await svc.feed(shop.store_id, clerk, NOV_1, NOV_30, today=TODAY) == []
    assert (await svc.today_summary(shop.store_id, clerk, today=TODAY)).overdue == []
    with pytest.raises(AgendaItemNotFound):
        await svc.set_occurrence_status(
            shop.store_id, clerk, series.id, date(2026, 11, 9), AgendaTaskStatus.DONE
        )
    mgr_days = [i.day for i in await svc.feed(shop.store_id, mgr, NOV_1, NOV_30, today=TODAY)]
    assert date(2026, 11, 2) in mgr_days


async def test_one_occurrence_cannot_change_visibility_on_its_own(
    db_session: AsyncSession,
) -> None:
    """只限管理者是整個重複待辦的設定；單獨改某一次，會跟規則對不起來。"""
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    series = await svc.create_series(
        shop.store_id,
        mgr,
        AgendaSeriesCreate(title="叫牛奶", starts_on=NOV_1, recurrence=_weekly(0)),
    )
    row = await svc.set_occurrence_status(
        shop.store_id, mgr, series.id, date(2026, 11, 2), AgendaTaskStatus.DONE
    )
    with pytest.raises(AgendaItemInvalid):
        await svc.update_task(shop.store_id, mgr, row.id, AgendaTaskUpdate(manager_only=True))


async def test_hidden_row_is_not_reachable_through_a_public_series(
    db_session: AsyncSession,
) -> None:
    """防呆：就算資料裡出現「規則公開、某一次隱藏」，店員也不能經由規則讀到或改到那一次。"""
    shop = await seed_store(db_session)
    mgr, clerk = _viewers(shop)
    svc = AgendaService(db_session)
    series = await svc.create_series(
        shop.store_id,
        mgr,
        AgendaSeriesCreate(title="叫牛奶", starts_on=NOV_1, recurrence=_weekly(0)),
    )
    row = await svc.update_occurrence(
        shop.store_id, mgr, series.id, date(2026, 11, 2), AgendaOccurrenceUpdate(note="私事")
    )
    hidden = await db_session.get(AgendaTask, row.id)
    assert hidden is not None
    hidden.manager_only = True
    await db_session.flush()

    with pytest.raises(AgendaItemNotFound):
        await svc.update_occurrence(
            shop.store_id, clerk, series.id, date(2026, 11, 2), AgendaOccurrenceUpdate()
        )
    with pytest.raises(AgendaItemNotFound):
        await svc.set_occurrence_status(
            shop.store_id, clerk, series.id, date(2026, 11, 2), AgendaTaskStatus.DONE
        )
    # 一鍵略過也不能順手改到看不到的那一次
    await svc.skip_overdue(shop.store_id, clerk, series.id, today=TODAY)
    assert hidden.status is AgendaTaskStatus.OPEN


async def test_event_update_checks_length_after_merging(db_session: AsyncSession) -> None:
    """Codex 1A 第二輪 P2：只改結束日也要檢查最長天數，不能撞到資料庫約束變成系統錯誤。"""
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    event = await svc.create_event(
        shop.store_id,
        mgr,
        AgendaEventCreate(kind=AgendaEventKind.MARKET, title="市集", starts_on=NOV_1),
    )
    with pytest.raises(AgendaItemInvalid):
        await svc.update_event(
            shop.store_id, mgr, event.id, AgendaEventUpdate(ends_on=date(2027, 12, 31))
        )


async def test_overdue_counts_every_missed_day_of_a_long_running_series(
    db_session: AsyncSession,
) -> None:
    """Codex 1A 第三輪 P2：用了好幾年的每天重複待辦，以前沒做的每一天都要算到、都能一鍵略過。"""
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    started = date(2020, 1, 1)
    series = await svc.create_series(
        shop.store_id,
        mgr,
        AgendaSeriesCreate(
            title="檢查冰箱溫度",
            starts_on=started,
            lead_days=0,
            recurrence=_weekly(0, 1, 2, 3, 4, 5, 6),
        ),
    )
    missed = (TODAY - started).days

    overdue = (await svc.today_summary(shop.store_id, mgr, today=TODAY)).overdue
    assert len(overdue) == missed
    assert overdue[-1].day == date(2026, 11, 9)

    assert await svc.skip_overdue(shop.store_id, mgr, series.id, today=TODAY) == missed
    assert (await svc.today_summary(shop.store_id, mgr, today=TODAY)).overdue == []


async def test_changing_only_the_end_date_keeps_the_schedule(db_session: AsyncSession) -> None:
    """Codex 1A 第四輪 P2：只改「重複到哪天」不重新起算——每幾個月的排程從開始月份數，
    一重算就整個錯開（二月起每兩個月，延長結束日後不能變成十一月、一月…）。"""
    shop = await seed_store(db_session)
    mgr, _ = _viewers(shop)
    svc = AgendaService(db_session)
    every_two = RecurrenceFields(
        kind=RecurrenceKind.EVERY_N_MONTHS, day_of_month=10, interval_months=2
    )
    series = await svc.create_series(
        shop.store_id,
        mgr,
        AgendaSeriesCreate(
            title="交發票給記帳士", starts_on=date(2026, 2, 1), recurrence=every_two
        ),
    )
    overdue_before = (await svc.today_summary(shop.store_id, mgr, today=TODAY)).overdue

    extended = await svc.update_series(
        shop.store_id,
        mgr,
        series.id,
        AgendaSeriesUpdate(recurrence=every_two.model_copy(update={"until": date(2027, 6, 30)})),
        today=TODAY,
    )
    assert extended.starts_on == date(2026, 2, 1)
    assert extended.recurrence.until == date(2027, 6, 30)
    assert extended.next_on == date(2026, 12, 10)
    feed = await svc.feed(shop.store_id, mgr, NOV_1, date(2027, 1, 31), today=TODAY)
    assert [i.day for i in feed] == [date(2026, 12, 10)]
    # 沒有重新起算，也就不必把以前沒做的那幾次另外建列：過期清單照舊
    overdue_after = (await svc.today_summary(shop.store_id, mgr, today=TODAY)).overdue
    assert [i.key for i in overdue_after] == [i.key for i in overdue_before]
