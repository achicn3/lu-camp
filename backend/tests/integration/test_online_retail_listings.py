"""線上「帶回家」零售商品（docs/63 §13、M1d）：從現有一般商品挑上線，不另存價格或庫存。

管理者可新增／修改／刪除、上傳照片，店員只能讀；同一商品只能上線一次；加購角色只能是咖啡豆／濾掛；
跨店商品一律找不到；每次變更寫稽核。
"""

import io
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import CatalogProduct, Category
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole

BASE = "/api/v1/online-order/retail"


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


async def seed(session: AsyncSession, name: str = "露坑") -> tuple[str, str, int]:
    """回傳（店員 token、管理者 token、咖啡豆商品 id）。"""
    store = Store(name=name)
    session.add(store)
    await session.flush()
    users = [
        User(store_id=store.id, username=f"{name}-{role.value}", password_hash="h", role=role)
        for role in (UserRole.CLERK, UserRole.MANAGER)
    ]
    category = Category(store_id=store.id, name="咖啡豆", target_margin_pct=40)
    session.add_all([*users, category])
    await session.flush()
    bean = CatalogProduct(
        store_id=store.id,
        sku=f"BEAN-{store.id}",
        name="耶加雪菲 200g",
        category_id=category.id,
        unit_price=Decimal(450),
        unit_cost=Decimal(220),
        quantity_on_hand=6,
    )
    session.add(bean)
    await session.flush()
    return (
        encode_access_token(user_id=users[0].id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=users[1].id, role="MANAGER", store_id=store.id),
        bean.id,
    )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (180, 120, 80)).save(buf, format="JPEG")
    return buf.getvalue()


async def test_manager_lists_a_product_online_with_its_own_price_and_stock(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, manager, bean = await seed(db_session)
    created = await client.post(
        BASE,
        json={"catalog_product_id": bean, "description": "柑橘、茉莉", "role": "bean"},
        headers=auth(manager),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["catalog_product_id"] == bean
    # 價格、庫存、分類一律來自原商品；不回成本
    assert (body["product_name"], body["unit_price"], body["quantity_on_hand"]) == (
        "耶加雪菲 200g",
        "450",
        6,
    )
    assert body["category_name"] == "咖啡豆"
    assert "unit_cost" not in body
    listed = await client.get(BASE, headers=auth(clerk))
    assert [row["id"] for row in listed.json()] == [body["id"]]
    audit = await db_session.scalars(
        select(AuditLog).where(AuditLog.entity_type == "online_retail_listing")
    )
    assert [a.action for a in audit] == ["CREATE_ONLINE_RETAIL_LISTING"]


async def test_clerk_cannot_change_listings(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, _manager, bean = await seed(db_session)
    resp = await client.post(BASE, json={"catalog_product_id": bean}, headers=auth(clerk))
    assert resp.status_code == 403


async def test_same_product_can_only_be_listed_once(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _clerk, manager, bean = await seed(db_session)
    assert (
        await client.post(BASE, json={"catalog_product_id": bean}, headers=auth(manager))
    ).status_code == 201
    again = await client.post(BASE, json={"catalog_product_id": bean}, headers=auth(manager))
    assert again.status_code == 409
    assert "已經上線" in again.json()["detail"]


async def test_upsell_role_is_bean_or_drip_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _clerk, manager, bean = await seed(db_session)
    resp = await client.post(
        BASE, json={"catalog_product_id": bean, "role": "coffee"}, headers=auth(manager)
    )
    assert resp.status_code == 422


async def test_other_store_product_is_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _clerk, manager, _bean = await seed(db_session)
    _c, _m, other_bean = await seed(db_session, "別家")
    resp = await client.post(BASE, json={"catalog_product_id": other_bean}, headers=auth(manager))
    assert resp.status_code == 404


async def test_update_photo_and_delete(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _clerk, manager, bean = await seed(db_session)
    listing = (
        await client.post(BASE, json={"catalog_product_id": bean}, headers=auth(manager))
    ).json()
    updated = await client.put(
        f"{BASE}/{listing['id']}",
        json={"catalog_product_id": bean, "description": "新介紹", "is_active": False},
        headers=auth(manager),
    )
    assert updated.status_code == 200, updated.text
    assert (updated.json()["description"], updated.json()["is_active"]) == ("新介紹", False)
    photo = await client.post(
        f"{BASE}/{listing['id']}/photo",
        files={"file": ("bean.jpg", _jpeg(), "image/jpeg")},
        headers=auth(manager),
    )
    assert photo.status_code == 200, photo.text
    sha = photo.json()["photo_sha256"]
    assert sha is not None
    # 照片沿用菜單照片的公開網址
    assert (await client.get(f"/api/v1/menu-photos/{sha}.webp")).status_code == 200
    cleared = await client.delete(f"{BASE}/{listing['id']}/photo", headers=auth(manager))
    assert cleared.json()["photo_sha256"] is None
    gone = await client.delete(f"{BASE}/{listing['id']}", headers=auth(manager))
    assert gone.status_code == 204
    assert (await client.get(BASE, headers=auth(manager))).json() == []
    missing = await client.delete(f"{BASE}/{listing['id']}", headers=auth(manager))
    assert missing.status_code == 404
