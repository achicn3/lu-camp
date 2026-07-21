"""盤點 API 整合測試：建盤點單（快照 system_qty）→ 輸入實點 → 確認調整 catalog 數量 + ADJUST 帳。

第一版只盤一般商品（catalog_products）。確認時即時重讀現量計算差額，避免清掉盤點期間的銷售。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import CatalogProduct, StockMovement
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import StockDirection, StockReason, UserRole


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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _seed_store(session: AsyncSession, *, name: str = "盤點店") -> tuple[str, int, int]:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    clerk = User(
        store_id=store.id, username=f"clk-{store.id}", password_hash="h", role=UserRole.CLERK
    )
    session.add(clerk)
    await session.flush()
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, store.id, clerk.id


async def _seed_catalog(session: AsyncSession, store_id: int, *, sku: str, qty: int) -> int:
    product = CatalogProduct(
        store_id=store_id,
        sku=sku,
        name=f"品-{sku}",
        unit_price=Decimal("100"),
        quantity_on_hand=qty,
    )
    session.add(product)
    await session.flush()
    return product.id


async def _adjust_count(session: AsyncSession, store_id: int, catalog_id: int) -> int:
    n = await session.scalar(
        select(func.count())
        .select_from(StockMovement)
        .where(
            StockMovement.store_id == store_id,
            StockMovement.catalog_product_id == catalog_id,
            StockMovement.direction == StockDirection.ADJUST,
            StockMovement.reason == StockReason.STOCKTAKE,
        )
    )
    return n or 0


async def test_create_stocktake_snapshots_catalog(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session)
    cat_id = await _seed_catalog(db_session, store_id, sku="A", qty=7)

    resp = await client.post("/api/v1/stocktakes", headers=_auth(token))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "DRAFT"
    line = next(line for line in body["lines"] if line["catalog_product_id"] == cat_id)
    assert line["system_qty"] == 7
    assert line["counted_qty"] is None


async def test_confirm_adjusts_quantity_and_writes_movement(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session)
    cat_id = await _seed_catalog(db_session, store_id, sku="A", qty=10)
    st = (await client.post("/api/v1/stocktakes", headers=_auth(token))).json()

    # 實點只有 8 → 短少 2，確認後現量校正為 8、寫一筆 ADJUST(-2)。
    resp = await client.post(
        f"/api/v1/stocktakes/{st['id']}/confirm",
        json={"counts": [{"catalog_product_id": cat_id, "counted_qty": 8}]},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "CONFIRMED"

    product = await db_session.get(CatalogProduct, cat_id)
    assert product is not None and product.quantity_on_hand == 8
    assert await _adjust_count(db_session, store_id, cat_id) == 1

    movement = await db_session.scalar(
        select(StockMovement).where(
            StockMovement.catalog_product_id == cat_id,
            StockMovement.direction == StockDirection.ADJUST,
        )
    )
    assert movement is not None and movement.qty == -2  # 短少 2（負差額）


async def test_confirm_uses_current_qty_not_stale_snapshot(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """盤點期間若有銷售（現量變動），確認以**當前現量**計差額，不以建單時快照，避免清掉銷售。"""
    token, store_id, _ = await _seed_store(db_session)
    cat_id = await _seed_catalog(db_session, store_id, sku="A", qty=10)
    st = (await client.post("/api/v1/stocktakes", headers=_auth(token))).json()

    # 盤點期間賣掉 1（現量 10→9）。
    product = await db_session.get(CatalogProduct, cat_id)
    assert product is not None
    product.quantity_on_hand = 9
    await db_session.flush()

    # 實點 8 → 相對「當前 9」差額 -1（非相對快照 10 的 -2）。
    resp = await client.post(
        f"/api/v1/stocktakes/{st['id']}/confirm",
        json={"counts": [{"catalog_product_id": cat_id, "counted_qty": 8}]},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    refreshed = await db_session.get(CatalogProduct, cat_id)
    assert refreshed is not None and refreshed.quantity_on_hand == 8
    movement = await db_session.scalar(
        select(StockMovement).where(
            StockMovement.catalog_product_id == cat_id,
            StockMovement.direction == StockDirection.ADJUST,
        )
    )
    assert movement is not None and movement.qty == -1


async def test_confirm_no_variance_writes_no_movement(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session)
    cat_id = await _seed_catalog(db_session, store_id, sku="A", qty=5)
    st = (await client.post("/api/v1/stocktakes", headers=_auth(token))).json()
    resp = await client.post(
        f"/api/v1/stocktakes/{st['id']}/confirm",
        json={"counts": [{"catalog_product_id": cat_id, "counted_qty": 5}]},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert await _adjust_count(db_session, store_id, cat_id) == 0  # 無差額 → 不寫帳


async def test_confirm_twice_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session)
    cat_id = await _seed_catalog(db_session, store_id, sku="A", qty=10)
    st = (await client.post("/api/v1/stocktakes", headers=_auth(token))).json()
    body = {"counts": [{"catalog_product_id": cat_id, "counted_qty": 9}]}
    first = await client.post(
        f"/api/v1/stocktakes/{st['id']}/confirm", json=body, headers=_auth(token)
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/v1/stocktakes/{st['id']}/confirm", json=body, headers=_auth(token)
    )
    assert second.status_code == 409, second.text
    # 不重複調整：ADJUST 帳仍只有一筆。
    assert await _adjust_count(db_session, store_id, cat_id) == 1


async def test_confirm_negative_count_returns_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session)
    cat_id = await _seed_catalog(db_session, store_id, sku="A", qty=10)
    st = (await client.post("/api/v1/stocktakes", headers=_auth(token))).json()
    resp = await client.post(
        f"/api/v1/stocktakes/{st['id']}/confirm",
        json={"counts": [{"catalog_product_id": cat_id, "counted_qty": -1}]},
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_get_and_list_stocktake(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token, store_id, _ = await _seed_store(db_session)
    await _seed_catalog(db_session, store_id, sku="A", qty=3)
    st_id = (await client.post("/api/v1/stocktakes", headers=_auth(token))).json()["id"]

    got = await client.get(f"/api/v1/stocktakes/{st_id}", headers=_auth(token))
    assert got.status_code == 200 and got.json()["id"] == st_id
    listed = await client.get("/api/v1/stocktakes", headers=_auth(token))
    assert listed.status_code == 200 and any(s["id"] == st_id for s in listed.json())
    missing = await client.get("/api/v1/stocktakes/999999", headers=_auth(token))
    assert missing.status_code == 404


async def test_confirm_cross_store_returns_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token_a, store_a, _ = await _seed_store(db_session, name="A 店")
    token_b, _store_b, _ = await _seed_store(db_session, name="B 店")
    await _seed_catalog(db_session, store_a, sku="A", qty=4)
    st_id = (await client.post("/api/v1/stocktakes", headers=_auth(token_a))).json()["id"]
    # B 店嘗試確認 A 店的盤點單 → 404。
    resp = await client.post(
        f"/api/v1/stocktakes/{st_id}/confirm", json={"counts": []}, headers=_auth(token_b)
    )
    assert resp.status_code == 404, resp.text
