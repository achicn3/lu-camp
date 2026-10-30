"""員工名單（docs/99 §4.4）。

管理者維護；店員只能看（待辦的負責人、第三期排班都從這份名單選）。
員工不一定要有系統帳號（裁示 6），有的話一個帳號只能對到一位員工。
"""

from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.main import create_app
from tests.integration.agenda_helpers import auth, seed_store

URL = "/api/v1/staff-members"


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


async def test_manager_maintains_staff_and_clerk_can_only_read(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)

    created = await client.post(
        URL, json={"name": "小王", "user_id": shop.clerk_id}, headers=auth(shop.manager_token)
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "小王"
    assert body["user_id"] == shop.clerk_id
    assert body["is_active"] is True

    # 沒有系統帳號的工讀生也能加
    part_timer = await client.post(
        URL, json={"name": "工讀生阿美"}, headers=auth(shop.manager_token)
    )
    assert part_timer.status_code == 201, part_timer.text
    assert part_timer.json()["user_id"] is None

    listed = await client.get(URL, headers=auth(shop.clerk_token))
    assert listed.status_code == 200, listed.text
    assert [m["name"] for m in listed.json()] == ["小王", "工讀生阿美"]

    assert (
        await client.post(URL, json={"name": "偷加"}, headers=auth(shop.clerk_token))
    ).status_code == 403
    assert (
        await client.patch(
            f"{URL}/{body['id']}", json={"name": "改名"}, headers=auth(shop.clerk_token)
        )
    ).status_code == 403


async def test_names_are_unique_per_store_and_trimmed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    other = await seed_store(db_session, name="二店")

    first = await client.post(URL, json={"name": "  小王 "}, headers=auth(shop.manager_token))
    assert first.status_code == 201, first.text
    assert first.json()["name"] == "小王"

    duplicate = await client.post(URL, json={"name": "小王"}, headers=auth(shop.manager_token))
    assert duplicate.status_code == 409, duplicate.text

    # 別家店同名不衝突
    elsewhere = await client.post(URL, json={"name": "小王"}, headers=auth(other.manager_token))
    assert elsewhere.status_code == 201, elsewhere.text

    blank = await client.post(URL, json={"name": "   "}, headers=auth(shop.manager_token))
    assert blank.status_code == 422, blank.text


async def test_user_link_must_be_same_store_and_one_to_one(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    other = await seed_store(db_session, name="二店")

    foreign = await client.post(
        URL, json={"name": "外人", "user_id": other.clerk_id}, headers=auth(shop.manager_token)
    )
    assert foreign.status_code == 422, foreign.text

    linked = await client.post(
        URL, json={"name": "小王", "user_id": shop.clerk_id}, headers=auth(shop.manager_token)
    )
    assert linked.status_code == 201, linked.text
    twice = await client.post(
        URL, json={"name": "小王分身", "user_id": shop.clerk_id}, headers=auth(shop.manager_token)
    )
    assert twice.status_code == 409, twice.text


async def test_patch_is_sparse_and_deactivated_staff_are_hidden_by_default(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    member_id = (
        await client.post(
            URL, json={"name": "小王", "user_id": shop.clerk_id}, headers=auth(shop.manager_token)
        )
    ).json()["id"]

    renamed = await client.patch(
        f"{URL}/{member_id}", json={"name": "王小明"}, headers=auth(shop.manager_token)
    )
    assert renamed.status_code == 200, renamed.text
    # 只改名字：帳號連結不能被順手清掉
    assert renamed.json()["user_id"] == shop.clerk_id

    unlinked = await client.patch(
        f"{URL}/{member_id}", json={"user_id": None}, headers=auth(shop.manager_token)
    )
    assert unlinked.json()["user_id"] is None

    deactivated = await client.patch(
        f"{URL}/{member_id}", json={"is_active": False}, headers=auth(shop.manager_token)
    )
    assert deactivated.json()["is_active"] is False

    assert (await client.get(URL, headers=auth(shop.manager_token))).json() == []
    everyone = await client.get(
        URL, params={"include_inactive": "true"}, headers=auth(shop.manager_token)
    )
    assert [m["name"] for m in everyone.json()] == ["王小明"]


async def test_other_store_cannot_touch_staff(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    shop = await seed_store(db_session)
    other = await seed_store(db_session, name="二店")
    member_id = (
        await client.post(URL, json={"name": "小王"}, headers=auth(shop.manager_token))
    ).json()["id"]

    stolen = await client.patch(
        f"{URL}/{member_id}", json={"name": "改掉"}, headers=auth(other.manager_token)
    )
    assert stolen.status_code == 404, stolen.text
    assert (await client.get(URL, headers=auth(other.manager_token))).json() == []


async def test_changes_are_audited(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    shop = await seed_store(db_session)
    member_id = (
        await client.post(URL, json={"name": "小王"}, headers=auth(shop.manager_token))
    ).json()["id"]
    await client.patch(
        f"{URL}/{member_id}", json={"is_active": False}, headers=auth(shop.manager_token)
    )

    rows = (
        await db_session.scalars(
            select(AuditLog)
            .where(AuditLog.entity_type == "staff_member", AuditLog.entity_id == str(member_id))
            .order_by(AuditLog.id)
        )
    ).all()
    assert [r.action for r in rows] == ["CREATE_STAFF_MEMBER", "UPDATE_STAFF_MEMBER"]
    assert rows[1].before == {"name": "小王", "user_id": None, "is_active": True}
    assert rows[1].after == {"name": "小王", "user_id": None, "is_active": False}
    assert rows[1].actor_user_id == shop.manager_id
