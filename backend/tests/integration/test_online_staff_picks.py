"""店員推薦（店主 2026-10-10 裁示）：線上發布分頁一份有序清單，
可挑餐飲品項、手沖體驗卡、帶著走商品；
客人掃碼直接進完整菜單，「店員推薦」排第一並預設打開。取代每個品項各自勾的「露坑推薦」。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.main import create_app
from app.modules.inventory.models import CatalogProduct
from tests.integration.test_online_menu_experiences import auth, brew_menu, card, seed

PICKS = "/api/v1/online-order/staff-picks"


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def session_override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = session_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _listed_bean(client: httpx.AsyncClient, session: AsyncSession, manager: str) -> int:
    """一個已上線（帶著走）的一般商品；回商品 id。"""
    me = (await client.get("/api/v1/auth/me", headers=auth(manager))).json()
    bean = CatalogProduct(
        store_id=me["store_id"],
        sku=f"PICK-{me['store_id']}",
        name="耶加雪菲豆",
        unit_price=Decimal("450"),
        quantity_on_hand=5,
    )
    session.add(bean)
    await session.flush()
    listed = await client.post(
        "/api/v1/online-order/retail", json={"catalog_product_id": bean.id}, headers=auth(manager)
    )
    assert listed.status_code == 201, listed.text
    return bean.id


async def test_empty_until_saved_and_only_managers_save(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, manager = await seed(db_session)
    ids = await brew_menu(client, manager)
    exp = await client.post(
        "/api/v1/online-order/experiences", json=card(ids), headers=auth(manager)
    )
    bean = await _listed_bean(client, db_session, manager)
    assert (await client.get(PICKS, headers=auth(clerk))).json() == {"items": []}

    body = {
        "items": [
            {"kind": "experience", "id": exp.json()["id"]},
            {"kind": "item", "id": ids["cake"]},
            {"kind": "retail", "id": bean},
        ]
    }
    assert (await client.put(PICKS, json=body, headers=auth(clerk))).status_code == 403
    saved = await client.put(PICKS, json=body, headers=auth(manager))
    assert saved.status_code == 200, saved.text
    assert (await client.get(PICKS, headers=auth(clerk))).json() == body  # 順序照存的

    log = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "UPDATE_ONLINE_STAFF_PICKS")
    )
    assert log is not None and log.after == body


async def test_picks_must_be_live_and_unique(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, manager = await seed(db_session)
    ids = await brew_menu(client, manager)
    _, other = await seed(db_session, name="別家")
    foreign = await brew_menu(client, other)
    archived = await client.delete(f"/api/v1/menu-items/{ids['cake']}", headers=auth(manager))
    assert archived.status_code == 200
    unlisted = CatalogProduct(
        store_id=(await client.get("/api/v1/auth/me", headers=auth(manager))).json()["store_id"],
        sku="NOT-LISTED",
        name="沒上線的",
        unit_price=Decimal("100"),
    )
    db_session.add(unlisted)
    await db_session.flush()

    for items in (
        [{"kind": "item", "id": foreign["brew"]}],
        [{"kind": "item", "id": ids["cake"]}],
        [{"kind": "experience", "id": 999_999}],
        [{"kind": "retail", "id": unlisted.id}],
        [{"kind": "item", "id": ids["brew"]}, {"kind": "item", "id": ids["brew"]}],
        [{"kind": "item", "id": ids["brew"]}] * 31,
    ):
        resp = await client.put(PICKS, json={"items": items}, headers=auth(manager))
        assert resp.status_code == 422, (items[:2], resp.text)
