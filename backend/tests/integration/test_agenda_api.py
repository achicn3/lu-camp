"""行事曆 API（docs/99 §4、§8、§9）：端點串接、權限、輸入檢查、跨店隔離。

日期邏輯（過期、快到期、重複規則）由 tests/test_agenda_service.py 以固定的「今天」測；
這裡用門市時區的今天往後推，只驗 API 行為。
"""

from collections.abc import AsyncGenerator
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.time import store_date, utc_now
from app.main import create_app
from tests.integration.agenda_helpers import auth, seed_store

API = "/api/v1/agenda"


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _day(offset: int) -> str:
    return (store_date(utc_now()) + timedelta(days=offset)).isoformat()


async def test_task_lifecycle(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    clerk = auth(shop.clerk_token)

    created = await client.post(
        f"{API}/tasks",
        json={"title": " 回覆廠商 ", "due_on": _day(2), "due_time": "15:30"},
        headers=clerk,
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert task["title"] == "回覆廠商"
    assert task["status"] == "OPEN"
    assert task["lead_days"] == 3

    patched = await client.patch(
        f"{API}/tasks/{task['id']}", json={"title": "回覆露營用品廠商"}, headers=clerk
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["due_time"] == "15:30:00"  # 沒送的欄位不動

    done = await client.post(
        f"{API}/tasks/{task['id']}/status", json={"status": "DONE"}, headers=clerk
    )
    assert done.status_code == 200, done.text
    assert done.json()["resolved_by_name"] == "門市-clk"

    reopened = await client.post(
        f"{API}/tasks/{task['id']}/status", json={"status": "OPEN"}, headers=clerk
    )
    assert reopened.json()["resolved_by_name"] is None

    deleted = await client.delete(f"{API}/tasks/{task['id']}", headers=clerk)
    assert deleted.status_code == 204, deleted.text
    assert (await client.get(f"{API}/tasks/{task['id']}", headers=clerk)).status_code == 404


async def test_undated_tasks_have_their_own_list(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    clerk = auth(shop.clerk_token)
    await client.post(f"{API}/tasks", json={"title": "找時間整理倉庫"}, headers=clerk)

    undated = await client.get(f"{API}/tasks/undated", headers=clerk)
    assert undated.status_code == 200, undated.text
    assert [t["title"] for t in undated.json()] == ["找時間整理倉庫"]


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "   "},
        {"title": "有時間沒日期", "due_time": "10:00"},
        {"title": "站外連結", "link_href": "https://evil.example"},
        {"title": "偷渡站外", "link_href": "//evil.example"},
        {"title": "提前太久", "due_on": "2026-11-10", "lead_days": 366},
    ],
)
async def test_invalid_task_input_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, payload: dict[str, object]
) -> None:
    shop = await seed_store(db_session)
    response = await client.post(f"{API}/tasks", json=payload, headers=auth(shop.clerk_token))
    assert response.status_code == 422, response.text


async def test_required_fields_cannot_be_cleared(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    clerk = auth(shop.clerk_token)
    task_id = (await client.post(f"{API}/tasks", json={"title": "補零錢"}, headers=clerk)).json()[
        "id"
    ]
    for body in ({"title": None}, {"manager_only": None}, {"lead_days": None}):
        response = await client.patch(f"{API}/tasks/{task_id}", json=body, headers=clerk)
        assert response.status_code == 422, (body, response.text)


async def test_manager_only_items_are_hidden_from_clerks(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    clerk, mgr = auth(shop.clerk_token), auth(shop.manager_token)

    forbidden = await client.post(
        f"{API}/tasks", json={"title": "偷藏", "manager_only": True}, headers=clerk
    )
    assert forbidden.status_code == 403, forbidden.text

    secret = await client.post(
        f"{API}/tasks",
        json={"title": "跟房東談續約", "due_on": _day(0), "manager_only": True},
        headers=mgr,
    )
    assert secret.status_code == 201, secret.text
    secret_id = secret.json()["id"]
    assert (await client.get(f"{API}/tasks/{secret_id}", headers=clerk)).status_code == 404
    assert (
        await client.post(f"{API}/tasks/{secret_id}/status", json={"status": "DONE"}, headers=clerk)
    ).status_code == 404

    params = {"start": _day(0), "end": _day(0)}
    clerk_feed = await client.get(f"{API}/items", params=params, headers=clerk)
    assert clerk_feed.json()["items"] == []
    mgr_feed = await client.get(f"{API}/items", params=params, headers=mgr)
    assert [i["title"] for i in mgr_feed.json()["items"]] == ["跟房東談續約"]


async def test_series_endpoints(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    clerk = auth(shop.clerk_token)
    start = store_date(utc_now()) - timedelta(days=14)
    every_day = {"kind": "WEEKLY", "weekdays": [0, 1, 2, 3, 4, 5, 6]}

    bad = await client.post(
        f"{API}/task-series",
        json={"title": "叫牛奶", "starts_on": start.isoformat(), "recurrence": {"kind": "WEEKLY"}},
        headers=clerk,
    )
    assert bad.status_code == 422, bad.text

    created = await client.post(
        f"{API}/task-series",
        json={"title": "叫牛奶", "starts_on": start.isoformat(), "recurrence": every_day},
        headers=clerk,
    )
    assert created.status_code == 201, created.text
    series = created.json()
    assert series["next_on"] == _day(0)
    listed = await client.get(f"{API}/task-series", headers=clerk)
    assert [s["title"] for s in listed.json()] == ["叫牛奶"]

    yesterday = _day(-1)
    done = await client.post(
        f"{API}/task-series/{series['id']}/occurrences/{yesterday}/status",
        json={"status": "DONE"},
        headers=clerk,
    )
    assert done.status_code == 200, done.text
    assert done.json()["occurrence_on"] == yesterday

    moved = await client.patch(
        f"{API}/task-series/{series['id']}/occurrences/{_day(1)}",
        json={"due_on": _day(2), "note": "改到後天"},
        headers=clerk,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["due_on"] == _day(2)

    today = await client.get(f"{API}/today", headers=clerk)
    assert today.status_code == 200, today.text
    assert len(today.json()["overdue"]) == 13  # 兩週前開始、每天一次，昨天做了

    skipped = await client.post(f"{API}/task-series/{series['id']}/skip-overdue", headers=clerk)
    assert skipped.json() == {"skipped": 13}
    assert (await client.get(f"{API}/today", headers=clerk)).json()["overdue"] == []

    renamed = await client.patch(
        f"{API}/task-series/{series['id']}", json={"title": "叫鮮奶"}, headers=clerk
    )
    assert renamed.json()["title"] == "叫鮮奶"

    deleted = await client.delete(f"{API}/task-series/{series['id']}", headers=clerk)
    assert deleted.status_code == 204, deleted.text
    gone = await client.get(f"{API}/task-series/{series['id']}", headers=clerk)
    assert gone.status_code == 404


async def test_occurrence_rows_cannot_be_deleted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    clerk = auth(shop.clerk_token)
    series_id = (
        await client.post(
            f"{API}/task-series",
            json={
                "title": "叫牛奶",
                "starts_on": _day(0),
                "recurrence": {"kind": "WEEKLY", "weekdays": [0, 1, 2, 3, 4, 5, 6]},
            },
            headers=clerk,
        )
    ).json()["id"]
    row = await client.post(
        f"{API}/task-series/{series_id}/occurrences/{_day(0)}/status",
        json={"status": "DONE"},
        headers=clerk,
    )
    deleted = await client.delete(f"{API}/tasks/{row.json()['id']}", headers=clerk)
    assert deleted.status_code == 422, deleted.text


async def test_event_endpoints(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    clerk = auth(shop.clerk_token)
    starts = store_date(utc_now())
    weekday = starts.weekday()

    created = await client.post(
        f"{API}/events",
        json={
            "kind": "CLOSED",
            "title": "公休",
            "starts_on": starts.isoformat(),
            "recurrence": {"kind": "WEEKLY", "weekdays": [weekday]},
        },
        headers=clerk,
    )
    assert created.status_code == 201, created.text
    event = created.json()
    assert event["ends_on"] == starts.isoformat()

    next_week = (starts + timedelta(days=7)).isoformat()
    cancelled = await client.post(
        f"{API}/events/{event['id']}/occurrences",
        json={"on": next_week, "cancelled": True},
        headers=clerk,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["cancelled_dates"] == [next_week]

    feed = await client.get(
        f"{API}/items",
        params={"start": starts.isoformat(), "end": (starts + timedelta(days=14)).isoformat()},
        headers=clerk,
    )
    assert [i["day"] for i in feed.json()["items"]] == [
        starts.isoformat(),
        (starts + timedelta(days=14)).isoformat(),
    ]

    renamed = await client.patch(
        f"{API}/events/{event['id']}", json={"title": "店休"}, headers=clerk
    )
    assert renamed.json()["title"] == "店休"
    assert renamed.json()["recurrence"]["weekdays"] == [weekday]  # 沒送的規則不動

    listed = await client.get(f"{API}/events", headers=clerk)
    assert [e["title"] for e in listed.json()] == ["店休"]
    assert (await client.delete(f"{API}/events/{event['id']}", headers=clerk)).status_code == 204
    assert (await client.get(f"{API}/events/{event['id']}", headers=clerk)).status_code == 404


async def test_event_input_checks(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    clerk = auth(shop.clerk_token)
    backwards = await client.post(
        f"{API}/events",
        json={"kind": "MARKET", "title": "市集", "starts_on": _day(2), "ends_on": _day(1)},
        headers=clerk,
    )
    assert backwards.status_code == 422, backwards.text

    single = await client.post(
        f"{API}/events",
        json={"kind": "MARKET", "title": "市集", "starts_on": _day(1)},
        headers=clerk,
    )
    not_repeating = await client.post(
        f"{API}/events/{single.json()['id']}/occurrences",
        json={"on": _day(1)},
        headers=clerk,
    )
    assert not_repeating.status_code == 422, not_repeating.text

    times = await client.post(
        f"{API}/events",
        json={
            "kind": "OTHER",
            "title": "盤點",
            "starts_on": _day(1),
            "start_time": "18:00",
            "end_time": "09:00",
        },
        headers=clerk,
    )
    assert times.status_code == 422, times.text


async def test_feed_range_checks_and_today_shape(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    clerk = auth(shop.clerk_token)
    await client.post(f"{API}/tasks", json={"title": "補發票紙", "due_on": _day(0)}, headers=clerk)

    backwards = await client.get(
        f"{API}/items", params={"start": _day(1), "end": _day(0)}, headers=clerk
    )
    assert backwards.status_code == 422, backwards.text
    too_long = await client.get(
        f"{API}/items", params={"start": _day(0), "end": _day(93)}, headers=clerk
    )
    assert too_long.status_code == 422, too_long.text

    today = await client.get(f"{API}/today", headers=clerk)
    assert today.status_code == 200, today.text
    body = today.json()
    assert body["today"] == _day(0)
    assert [i["title"] for i in body["due_today"]] == ["補發票紙"]
    assert body["badge_count"] == 1
    assert body["consignment_pending_count"] == 0


async def test_other_store_cannot_see_or_touch(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    other = await seed_store(db_session, name="二店")
    task_id = (
        await client.post(
            f"{API}/tasks",
            json={"title": "盤點", "due_on": _day(0)},
            headers=auth(shop.manager_token),
        )
    ).json()["id"]

    outsider = auth(other.manager_token)
    assert (await client.get(f"{API}/tasks/{task_id}", headers=outsider)).status_code == 404
    assert (
        await client.patch(f"{API}/tasks/{task_id}", json={"title": "改掉"}, headers=outsider)
    ).status_code == 404
    feed = await client.get(
        f"{API}/items", params={"start": _day(0), "end": _day(0)}, headers=outsider
    )
    assert feed.json()["items"] == []


async def test_assignee_must_be_active_staff(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    member = await client.post(
        "/api/v1/staff-members", json={"name": "小王"}, headers=auth(shop.manager_token)
    )
    member_id = member.json()["id"]

    assigned = await client.post(
        f"{API}/tasks",
        json={"title": "盤點", "due_on": _day(0), "assignee_staff_id": member_id},
        headers=auth(shop.clerk_token),
    )
    assert assigned.status_code == 201, assigned.text
    assert assigned.json()["assignee_name"] == "小王"

    unknown = await client.post(
        f"{API}/tasks",
        json={"title": "盤點", "assignee_staff_id": member_id + 999},
        headers=auth(shop.clerk_token),
    )
    assert unknown.status_code == 422, unknown.text
