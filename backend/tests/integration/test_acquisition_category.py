"""F6 A3.5：收購品項 category_id additive 持久化（落地、跨店守衛、選填向後相容）。"""

import itertools
from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole

_idem = itertools.count()


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


async def _store_token(session: AsyncSession, name: str) -> tuple[int, str]:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username=f"c{store.id}", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    return store.id, encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"k-{next(_idem)}"}


async def _seller(client: httpx.AsyncClient, token: str) -> int:
    resp = await client.post(
        "/api/v1/contacts",
        json={"name": "賣家", "roles": ["SELLER"], "national_id": "A123456789"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _category(client: httpx.AsyncClient, token: str, name: str) -> int:
    resp = await client.post("/api/v1/categories", json={"name": name}, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return int(resp.json()["id"])


async def _open_drawer(client: httpx.AsyncClient, token: str) -> None:
    resp = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    assert resp.status_code == 201


async def test_buyout_persists_category_id(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _sid, token = await _store_token(db_session, "店A")
    await _open_drawer(client, token)
    seller = await _seller(client, token)
    cat = await _category(client, token, "登山服飾")

    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BUYOUT",
            "contact_id": seller,
            "items": [
                {
                    "name": "外套",
                    "grade": "A",
                    "listed_price": "3000",
                    "acquisition_cost": "1200",
                    "category_id": cat,
                }
            ],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    code = resp.json()["item_codes"][0]
    item = await db_session.scalar(select(SerializedItem).where(SerializedItem.item_code == code))
    assert item is not None and item.category_id == cat


async def test_buyout_without_category_still_works(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """additive：既有不帶 category_id 的收購仍成立，落地 category_id 為 NULL。"""
    _sid, token = await _store_token(db_session, "店A")
    await _open_drawer(client, token)
    seller = await _seller(client, token)
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BUYOUT",
            "contact_id": seller,
            "items": [{"name": "相機", "grade": "A", "listed_price": "3000",
                       "acquisition_cost": "1800"}],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201
    code = resp.json()["item_codes"][0]
    item = await db_session.scalar(select(SerializedItem).where(SerializedItem.item_code == code))
    assert item is not None and item.category_id is None


async def test_cross_store_category_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _a, token_a = await _store_token(db_session, "店A")
    _b, token_b = await _store_token(db_session, "店B")
    cat_b = await _category(client, token_b, "他店分類")  # 屬店B
    await _open_drawer(client, token_a)
    seller_a = await _seller(client, token_a)

    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BUYOUT",
            "contact_id": seller_a,
            "items": [{"name": "外套", "grade": "A", "listed_price": "3000",
                       "acquisition_cost": "1200", "category_id": cat_b}],
        },
        headers=_auth(token_a),
    )
    assert resp.status_code == 422  # InvalidAcquisitionCategory


async def test_bulk_lot_category_optional(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _sid, token = await _store_token(db_session, "店A")
    await _open_drawer(client, token)
    seller = await _seller(client, token)
    cat = await _category(client, token, "雜物")

    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BULK_LOT",
            "contact_id": seller,
            "lot": {
                "name": "雜物堆",
                "acquisition_cost": "300",
                "acquisition_basis": "BAG",
                "total_qty": 10,
                "unit_price": "50",
                "category_id": cat,
            },
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    lot_code = resp.json()["lot_code"]
    lot = await db_session.scalar(select(BulkLot).where(BulkLot.lot_code == lot_code))
    assert lot is not None and lot.category_id == cat
