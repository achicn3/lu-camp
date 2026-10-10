"""Manager menu presentation settings persist without replacing menu product data."""

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole

DEFAULTS = {
    "flavor_description": None,
    "audience_description": None,
    "is_new": False,
    "limited_on": None,
    "show_remaining": True,
    "low_stock_threshold": 5,
    "hide_sold_out": False,
    "role": None,
}
SETTINGS = {
    **DEFAULTS,
    "flavor_description": "蜜桃、花香",
    "audience_description": "喜歡清爽果香的你",
    "is_new": True,
    "limited_on": "2026-10-06",
    "show_remaining": False,
    "low_stock_threshold": 3,
    "hide_sold_out": True,
}


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def session_override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = session_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def seed(session: AsyncSession, *, name: str = "露坑") -> tuple[str, str]:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    users = [
        User(store_id=store.id, username=f"{name}-{role.value}", password_hash="h", role=role)
        for role in (UserRole.CLERK, UserRole.MANAGER)
    ]
    session.add_all(users)
    await session.flush()
    return (
        encode_access_token(user_id=users[0].id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=users[1].id, role="MANAGER", store_id=store.id),
    )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def item(client: httpx.AsyncClient, manager: str) -> int:
    response = await client.post(
        "/api/v1/menu-items",
        headers=auth(manager),
        json={"name": "蜜桃手沖", "unit_price": "180", "unit_cost": "50"},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def path(item_id: int) -> str:
    return f"/api/v1/online-order/menu-items/{item_id}/presentation"


async def test_presentation_defaults_roundtrip_reset_and_audit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, manager = await seed(db_session)
    item_id = await item(client, manager)
    initial = await client.get(path(item_id), headers=auth(clerk))
    assert initial.status_code == 200, initial.text
    assert initial.json() == {"menu_item_id": item_id, **DEFAULTS}
    saved = await client.put(path(item_id), headers=auth(manager), json=SETTINGS)
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"menu_item_id": item_id, **SETTINGS}
    assert (await client.get(path(item_id), headers=auth(clerk))).json() == saved.json()
    reset = await client.put(path(item_id), headers=auth(manager), json=DEFAULTS)
    assert reset.status_code == 200, reset.text
    assert reset.json() == initial.json()
    logs = list(
        await db_session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "UPDATE_ONLINE_MENU_PRESENTATION")
            .order_by(AuditLog.id)
        )
    )
    assert len(logs) == 2
    assert logs[0].before == DEFAULTS
    assert logs[0].after == SETTINGS
    assert logs[1].before == SETTINGS
    assert logs[1].after == DEFAULTS


async def test_clerk_cannot_update_presentation(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, manager = await seed(db_session)
    item_id = await item(client, manager)
    response = await client.put(path(item_id), headers=auth(clerk), json=SETTINGS)
    assert response.status_code == 403
    assert (await client.get(path(item_id), headers=auth(manager))).json() == {
        "menu_item_id": item_id,
        **DEFAULTS,
    }


@pytest.mark.parametrize("method", ["GET", "PUT"])
async def test_presentation_store_scope(
    client: httpx.AsyncClient, db_session: AsyncSession, method: str
) -> None:
    _, owner = await seed(db_session)
    _, outsider = await seed(db_session, name="別家")
    item_id = await item(client, owner)
    assert (await client.put(path(item_id), headers=auth(owner), json=SETTINGS)).status_code == 200
    response = await client.request(
        method,
        path(item_id),
        headers=auth(outsider),
        json=DEFAULTS if method == "PUT" else None,
    )
    assert response.status_code == 404


@pytest.mark.parametrize("method", ["GET", "PUT"])
@pytest.mark.parametrize("archived", [False, True])
async def test_missing_and_archived_items_are_not_configurable(
    client: httpx.AsyncClient, db_session: AsyncSession, method: str, archived: bool
) -> None:
    _, manager = await seed(db_session)
    item_id = 999999
    if archived:
        item_id = await item(client, manager)
        assert (
            await client.put(path(item_id), headers=auth(manager), json=SETTINGS)
        ).status_code == 200
        response = await client.delete(f"/api/v1/menu-items/{item_id}", headers=auth(manager))
        assert response.status_code == 200, response.text
    response = await client.request(
        method,
        path(item_id),
        headers=auth(manager),
        json=SETTINGS if method == "PUT" else None,
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "invalid",
    [
        {"flavor_description": "香" * 121},
        {"audience_description": "你" * 121},
        {"low_stock_threshold": -1},
        {"low_stock_threshold": 10000},
        {"low_stock_threshold": 2.5},
        {"low_stock_threshold": True},
        {"low_stock_threshold": "5"},
        {"low_stock_threshold": None},
        {"is_recommended": True},  # 舊欄位：改由「店員推薦」清單選（2026-10-10）
        {"is_new": 1},
        {"show_remaining": None},
        {"hide_sold_out": "true"},
        {"limited_on": "2026-02-30"},
        {"unit_price": "1"},
        {"unit_cost": "1"},
        {"remaining": 999},
        {"store_id": 1},
    ],
)
async def test_invalid_presentation_rejected_without_changing_saved_settings(
    client: httpx.AsyncClient, db_session: AsyncSession, invalid: dict[str, object]
) -> None:
    _, manager = await seed(db_session)
    item_id = await item(client, manager)
    assert (
        await client.put(path(item_id), headers=auth(manager), json=SETTINGS)
    ).status_code == 200
    response = await client.put(path(item_id), headers=auth(manager), json={**SETTINGS, **invalid})
    assert response.status_code == 422, response.text
    assert (await client.get(path(item_id), headers=auth(manager))).json() == {
        "menu_item_id": item_id,
        **SETTINGS,
    }


@pytest.mark.parametrize("threshold", [0, 9999])
async def test_boundary_settings_and_empty_put_restores_defaults(
    client: httpx.AsyncClient, db_session: AsyncSession, threshold: int
) -> None:
    _, manager = await seed(db_session)
    item_id = await item(client, manager)
    settings = {**SETTINGS, "low_stock_threshold": threshold, "flavor_description": "香" * 120}
    response = await client.put(path(item_id), headers=auth(manager), json=settings)
    assert response.status_code == 200, response.text
    assert response.json() == {"menu_item_id": item_id, **settings}
    response = await client.put(path(item_id), headers=auth(manager), json={})
    assert response.status_code == 200
    assert response.json() == {"menu_item_id": item_id, **DEFAULTS}
    source = (await client.get("/api/v1/menu-items", headers=auth(manager))).json()[0]
    assert source["unit_price"] == "180"
    assert source["unit_cost"] == "50"


async def test_menu_item_hard_delete_cascades_presentation(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, manager = await seed(db_session)
    item_id = await item(client, manager)
    assert (
        await client.put(path(item_id), headers=auth(manager), json=SETTINGS)
    ).status_code == 200
    response = await client.delete(f"/api/v1/menu-items/{item_id}/delete", headers=auth(manager))
    assert response.status_code == 204, response.text
    assert (await client.get(path(item_id), headers=auth(manager))).status_code == 404
