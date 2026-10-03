"""組合包袋裝條碼 API（ADR-028）：管理者建立／列出／停用，店員掃碼；跨店一律 404。"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import CatalogProduct
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole


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


async def _store(session: AsyncSession, name: str) -> dict[str, object]:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    users = {}
    for role in (UserRole.MANAGER, UserRole.CLERK):
        user = User(
            store_id=store.id, username=f"{role.value}{store.id}", password_hash="h", role=role
        )
        session.add(user)
        await session.flush()
        users[role] = encode_access_token(user_id=user.id, role=role.value, store_id=store.id)
    coffee = CatalogProduct(
        store_id=store.id,
        sku=f"BIRD{store.id}",
        name="天堂鳥濾掛",
        unit_price=Decimal(50),
        quantity_on_hand=144,
    )
    session.add(coffee)
    await session.flush()
    return {
        "id": store.id,
        "manager": users[UserRole.MANAGER],
        "clerk": users[UserRole.CLERK],
        "coffee": coffee.id,
    }


def _auth(token: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _bundle_campaign(client: httpx.AsyncClient, store: dict[str, object]) -> int:
    now = datetime.now(UTC)
    target = {"target_type": "CATALOG_PRODUCT", "target_id": store["coffee"]}
    created = await client.post(
        "/api/v1/campaigns",
        json={
            "name": "濾掛 12 入",
            "kind": "BUNDLE",
            "bundle_price": "500",
            "bundle_slots": [{"qty": 6, "targets": [target]}, {"qty": 6, "targets": [target]}],
            "starts_at": (now - timedelta(days=1)).isoformat(),
            "ends_at": (now + timedelta(days=1)).isoformat(),
            "applies_owned_serialized": True,
            "applies_owned_bulk": True,
            "applies_catalog": True,
            "applies_consignment": False,
        },
        headers=_auth(store["manager"]),
    )
    assert created.status_code == 201, created.text
    campaign_id = int(created.json()["id"])
    activated = await client.post(
        f"/api/v1/campaigns/{campaign_id}/activate", headers=_auth(store["manager"])
    )
    assert activated.status_code == 200, activated.text
    return campaign_id


async def test_create_list_scan_deactivate(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store = await _store(db_session, "店A")
    other = await _store(db_session, "店B")
    campaign = await _bundle_campaign(client, store)
    body = {
        "name": "濾掛 12 入袋",
        "items": [{"item_type": "CATALOG", "target_id": store["coffee"], "qty": 12}],
    }

    # 店員不能建；管理者可以
    clerk = await client.post(
        f"/api/v1/campaigns/{campaign}/packs", json=body, headers=_auth(store["clerk"])
    )
    assert clerk.status_code == 403
    created = await client.post(
        f"/api/v1/campaigns/{campaign}/packs", json=body, headers=_auth(store["manager"])
    )
    assert created.status_code == 201, created.text
    pack = created.json()
    assert pack["items"] == [
        {"item_type": "CATALOG", "target_id": store["coffee"], "qty": 12, "label": "天堂鳥濾掛"}
    ]

    listed = await client.get(
        f"/api/v1/campaigns/{campaign}/packs", headers=_auth(store["manager"])
    )
    assert [p["code"] for p in listed.json()] == [pack["code"]]

    # 店員掃碼；金額是字串
    scan = await client.get(
        f"/api/v1/bundle-packs/by-code/{pack['code']}", headers=_auth(store["clerk"])
    )
    assert scan.status_code == 200, scan.text
    data = scan.json()
    assert data["campaign_effective"] is True and data["bundle_price"] == "500"
    assert [(i["code"], i["qty"], i["unit_price"], i["available"]) for i in data["items"]] == [
        (f"BIRD{store['id']}", 12, "50", True)
    ]

    # 別家掃不到、建不了、停用不了
    assert (
        await client.get(
            f"/api/v1/bundle-packs/by-code/{pack['code']}", headers=_auth(other["clerk"])
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/campaigns/{campaign}/packs", json=body, headers=_auth(other["manager"])
        )
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/bundle-packs/{pack['id']}/deactivate", headers=_auth(other["manager"])
        )
    ).status_code == 404

    off = await client.post(
        f"/api/v1/bundle-packs/{pack['id']}/deactivate", headers=_auth(store["manager"])
    )
    assert off.status_code == 200 and off.json()["is_active"] is False
    assert (
        await client.get(
            f"/api/v1/bundle-packs/by-code/{pack['code']}", headers=_auth(store["clerk"])
        )
    ).status_code == 404


async def test_invalid_contents_are_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store = await _store(db_session, "店A")
    campaign = await _bundle_campaign(client, store)
    short = await client.post(
        f"/api/v1/campaigns/{campaign}/packs",
        json={
            "name": "少一包",
            "items": [{"item_type": "CATALOG", "target_id": store["coffee"], "qty": 11}],
        },
        headers=_auth(store["manager"]),
    )
    assert short.status_code == 422 and "湊不成" in short.json()["detail"]
    serialized_two = await client.post(
        f"/api/v1/campaigns/{campaign}/packs",
        json={"name": "x", "items": [{"item_type": "SERIALIZED", "target_id": 1, "qty": 2}]},
        headers=_auth(store["manager"]),
    )
    assert serialized_two.status_code == 422
