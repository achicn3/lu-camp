"""一般商品手動調整數量（店主 2026-10-24）。

原本一般商品的數量只能靠採購收貨或盤點改；破損、過期、自用、點錯要改一個數字得開整張盤點單。
編輯視窗直接改數量、**一定要選原因**，庫存價值（數量 × 進價）跟著變——不另記損耗支出。

- 送出時帶畫面上看到的數量（`expected_qty`）：期間有人賣掉就擋下，不蓋掉那筆銷售。
- 破損／過期／自用只能減少；數量更正／其他可增可減。
- 寫庫存異動帳（明細歷史看得到原因）與稽核（前後數量、原因、備註）。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
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


async def _seed(session: AsyncSession) -> tuple[str, str, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add_all([mgr, clerk])
    # commit：被擋下的請求會 rollback，沒 commit 的種子資料會一起被捲掉。
    await session.commit()
    return (
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        store.id,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _product(session: AsyncSession, store_id: int, *, qty: int = 5) -> int:
    product = CatalogProduct(
        store_id=store_id,
        sku="SKU-1",
        name="罐裝咖啡",
        unit_price=Decimal(100),
        unit_cost=Decimal(60),
        quantity_on_hand=qty,
    )
    session.add(product)
    await session.commit()
    return product.id


async def _adjust(
    client: httpx.AsyncClient, token: str, product_id: int, **body: object
) -> httpx.Response:
    return await client.post(
        f"/api/v1/catalog-products/{product_id}/quantity-adjustments",
        json=body,
        headers=_auth(token),
    )


async def _movements(session: AsyncSession, product_id: int) -> list[StockMovement]:
    rows = await session.scalars(
        select(StockMovement).where(StockMovement.catalog_product_id == product_id)
    )
    return list(rows)


async def test_decrease_with_reason_records_movement_and_audit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id)

    resp = await _adjust(
        client, mgr, product_id, expected_qty=5, new_qty=3, reason="DAMAGED", note="摔破兩罐"
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["quantity_on_hand"] == 3
    [movement] = await _movements(db_session, product_id)
    assert movement.direction is StockDirection.OUT
    assert movement.qty == 2
    assert movement.reason is StockReason.DAMAGED
    assert movement.store_id == store_id
    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "ADJUST_CATALOG_QUANTITY")
    )
    assert audit is not None
    assert audit.entity_id == str(product_id)
    assert audit.before == {"quantity_on_hand": 5}
    assert audit.after == {"quantity_on_hand": 3, "reason": "DAMAGED", "note": "摔破兩罐"}


async def test_count_correction_can_increase(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id)

    resp = await _adjust(
        client, mgr, product_id, expected_qty=5, new_qty=8, reason="COUNT_CORRECTION"
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["quantity_on_hand"] == 8
    [movement] = await _movements(db_session, product_id)
    assert movement.direction is StockDirection.IN
    assert movement.qty == 3
    assert movement.reason is StockReason.COUNT_CORRECTION


async def test_loss_reasons_cannot_increase(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id)

    for reason in ("DAMAGED", "EXPIRED", "SELF_USE"):
        resp = await _adjust(client, mgr, product_id, expected_qty=5, new_qty=6, reason=reason)
        assert resp.status_code == 422, reason

    product = await db_session.get(CatalogProduct, product_id)
    assert product is not None
    assert product.quantity_on_hand == 5
    assert await _movements(db_session, product_id) == []


async def test_stale_expected_qty_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """編輯視窗開著時有人賣掉一件：照舊數字蓋回去會把那筆銷售吃掉。"""
    mgr, _clerk, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id, qty=4)

    resp = await _adjust(client, mgr, product_id, expected_qty=5, new_qty=3, reason="EXPIRED")

    assert resp.status_code == 409
    assert "4" in resp.json()["detail"]
    product = await db_session.get(CatalogProduct, product_id)
    assert product is not None
    assert product.quantity_on_hand == 4


async def test_unchanged_or_negative_qty_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id)

    same = await _adjust(client, mgr, product_id, expected_qty=5, new_qty=5, reason="OTHER_ADJUST")
    negative = await _adjust(
        client, mgr, product_id, expected_qty=5, new_qty=-1, reason="OTHER_ADJUST"
    )
    bad_reason = await _adjust(client, mgr, product_id, expected_qty=5, new_qty=4, reason="SALE")

    assert same.status_code == 422
    assert negative.status_code == 422
    assert bad_reason.status_code == 422
    assert await _movements(db_session, product_id) == []


async def test_clerk_cannot_adjust(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _mgr, clerk, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id)

    resp = await _adjust(client, clerk, product_id, expected_qty=5, new_qty=4, reason="SELF_USE")

    assert resp.status_code == 403


async def test_other_store_product_is_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store_id = await _seed(db_session)
    other = Store(name="別家")
    db_session.add(other)
    await db_session.flush()
    product_id = await _product(db_session, other.id)

    resp = await _adjust(client, mgr, product_id, expected_qty=5, new_qty=4, reason="SELF_USE")

    assert resp.status_code == 404
    product = await db_session.get(CatalogProduct, product_id)
    assert product is not None
    assert product.quantity_on_hand == 5


async def test_inventory_value_and_history_follow_adjustment(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """庫存價值 = 數量 × 進價，改了數量成本就跟著扣；明細歷史看得到原因。"""
    mgr, _clerk, store_id = await _seed(db_session)
    product_id = await _product(db_session, store_id)

    await _adjust(client, mgr, product_id, expected_qty=5, new_qty=2, reason="SELF_USE")

    report = await client.get("/api/v1/reports/inventory-value", headers=_auth(mgr))
    assert report.json()["catalog_total_qty"] == 2
    assert report.json()["catalog_cost_value"] == "120"
    detail = await client.get(f"/api/v1/catalog-products/{product_id}/detail", headers=_auth(mgr))
    events = [(h["event"], h["qty"]) for h in detail.json()["history"]]
    assert ("自用", 3) in events
