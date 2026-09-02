"""收購/建檔當下就能寫商品備註（2026-09-02 裁示）。

驗機時直接記「附原廠盒、缺充電線」，不必事後回頭找那件商品。三種庫存型態一致。
"""

import itertools
from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import BulkLot, CatalogProduct, SerializedItem
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
    user = User(
        store_id=store.id, username=f"u{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    session.add(user)
    await session.flush()
    return store.id, encode_access_token(
        user_id=user.id, role=UserRole.MANAGER.value, store_id=store.id
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"k-{next(_idem)}"}


async def _seller(client: httpx.AsyncClient, token: str) -> int:
    resp = await client.post(
        "/api/v1/contacts",
        json={
            "name": "賣家",
            "phone": f"09{next(_idem):08d}",
            "roles": ["SELLER"],
            "national_id": "A123456789",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _open_drawer(client: httpx.AsyncClient, token: str) -> None:
    resp = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    assert resp.status_code == 201


async def test_buyout_persists_item_note(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """買斷逐件備註落地，且掃碼查件即帶回（POS 結帳提醒的資料來源）。"""
    _sid, token = await _store_token(db_session, "店A")
    await _open_drawer(client, token)
    seller = await _seller(client, token)

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
                    "note": "  右袖口有磨損，附原廠吊牌  ",
                }
            ],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    code = resp.json()["item_codes"][0]
    item = await db_session.scalar(select(SerializedItem).where(SerializedItem.item_code == code))
    assert item is not None
    assert item.note == "右袖口有磨損，附原廠吊牌"  # 前後空白已在 schema 修剪
    read = await client.get(f"/api/v1/serialized-items/by-code/{code}", headers=_auth(token))
    assert read.status_code == 200, read.text
    assert read.json()["note"] == "右袖口有磨損，附原廠吊牌"


async def test_acquisition_without_note_is_null(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """備註選填：沒填就是 NULL，不可變成空字串（否則 POS 會跳空提醒）。"""
    _sid, token = await _store_token(db_session, "店B")
    await _open_drawer(client, token)
    seller = await _seller(client, token)
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BUYOUT",
            "contact_id": seller,
            "items": [
                {"name": "帳篷", "grade": "B", "listed_price": "2000", "acquisition_cost": "800"}
            ],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    code = resp.json()["item_codes"][0]
    item = await db_session.scalar(select(SerializedItem).where(SerializedItem.item_code == code))
    assert item is not None and item.note is None


async def test_bulk_lot_persists_note(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """散裝批同樣可在收購當下寫備註。"""
    _sid, token = await _store_token(db_session, "店C")
    await _open_drawer(client, token)
    seller = await _seller(client, token)
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BULK_LOT",
            "contact_id": seller,
            "lot": {
                "name": "營釘一批",
                "acquisition_cost": "500",
                "acquisition_basis": "BAG",
                "total_qty": 20,
                "unit_price": "50",
                "note": "數量請客人自己點過",
            },
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    lot_code = resp.json()["lot_code"]
    lot = await db_session.scalar(select(BulkLot).where(BulkLot.lot_code == lot_code))
    assert lot is not None and lot.note == "數量請客人自己點過"


async def test_catalog_product_create_persists_note(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """一般商品建檔也能帶備註（採購品沒有收購單，只能在這裡寫）。"""
    _sid, token = await _store_token(db_session, "店D")
    resp = await client.post(
        "/api/v1/catalog-products",
        json={"name": "高山瓦斯罐", "unit_price": "150", "note": "效期短，先進先出"},
        headers=_auth(token),
    )
    assert resp.status_code in (200, 201), resp.text
    product_id = int(resp.json()["id"])
    product = await db_session.scalar(
        select(CatalogProduct).where(CatalogProduct.id == product_id)
    )
    assert product is not None and product.note == "效期短，先進先出"
    assert resp.json()["note"] == "效期短，先進先出"
