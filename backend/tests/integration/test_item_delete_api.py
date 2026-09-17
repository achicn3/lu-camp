"""庫存與菜單品項的刪除（2026-09-17 裁示）。

混合制：**沒賣過真刪、賣過只能下架**。誤建（打錯字、重複建）就該從清單消失得乾乾淨淨，
全走封存的話清單會愈積愈多垃圾；但已成交的硬刪會讓交易紀錄與毛利報表指向不存在的商品。

畫面上不要求店員自己分辨——一律按刪除，不能刪的由後端回 409 並說明原因。
"""

import json
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import BulkLot, CatalogProduct, SerializedItem, StockMovement
from app.modules.menu.models import MenuItem
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    Grade,
    OwnershipType,
    SerializedItemStatus,
    UserRole,
)
from tests.integration.customer_display_helpers import CustomerDisplayAwareClient


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with CustomerDisplayAwareClient(
        transport=transport, base_url="http://test", db_session=db_session
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def _seed(session: AsyncSession) -> tuple[str, str, int]:
    """建店＋經理（開帳）＋店員，回 (mgr_token, clerk_token, store_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add_all([mgr, clerk])
    await session.flush()
    await CashDrawerService(session).open_session(store.id, mgr.id, Decimal(1000))
    return (
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        store.id,
    )


def _auth(token: str, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


async def _serialized(
    session: AsyncSession, store_id: int, *, code: str, acquisition: bool = False
) -> int:
    item = SerializedItem(
        store_id=store_id,
        item_code=code,
        name="誤建的帳篷",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        acquisition_cost=Decimal(500),
        listed_price=Decimal(1000),
        status=SerializedItemStatus.IN_STOCK,
    )
    if acquisition:
        item.acquisition_id = await _acquisition_id(session, store_id)
    session.add(item)
    await session.flush()
    session.add(
        StockMovement(
            store_id=store_id,
            item_kind="SERIALIZED",
            serialized_item_id=item.id,
            direction="IN",
            qty=1,
            reason="ACQUISITION",
        )
    )
    await session.flush()
    return item.id


async def _acquisition_id(session: AsyncSession, store_id: int) -> int:
    """最小的一張收購單（只為了讓品項有來源可指）。"""
    from sqlalchemy import text

    user_id = await session.scalar(select(User.id).where(User.store_id == store_id))
    contact_id = await session.scalar(
        text(
            "INSERT INTO contacts (store_id, name, roles, national_id_enc, created_at, updated_at)"
            " VALUES (:s, '賣方', ARRAY['SELLER'], 'enc', now(), now()) RETURNING id"
        ),
        {"s": store_id},
    )
    acq_id = await session.scalar(
        text(
            "INSERT INTO acquisitions (store_id, type, contact_id, clerk_user_id,"
            " total_cash_paid, payout_method, payout_cash_amount, created_at, updated_at)"
            " VALUES (:s, 'BUYOUT', :c, :u, 500, 'CASH', 500, now(), now()) RETURNING id"
        ),
        {"s": store_id, "c": contact_id, "u": user_id},
    )
    assert acq_id is not None
    return int(acq_id)


async def _catalog(session: AsyncSession, store_id: int, *, sku: str) -> int:
    product = CatalogProduct(
        store_id=store_id, sku=sku, name="誤建的瓦斯罐", unit_price=Decimal(100), quantity_on_hand=5
    )
    session.add(product)
    await session.flush()
    return product.id


async def _bulk(session: AsyncSession, store_id: int, *, code: str) -> int:
    lot = BulkLot(
        store_id=store_id,
        lot_code=code,
        name="誤建的雜物堆",
        grade=Grade.E,
        acquisition_cost=Decimal(300),
        acquisition_basis=BulkAcquisitionBasis.BAG,
        unit_price=Decimal(50),
        total_qty=10,
        remaining_qty=10,
        status=BulkLotStatus.ON_SALE,
    )
    session.add(lot)
    await session.flush()
    return lot.id


async def _menu(session: AsyncSession, store_id: int, *, name: str) -> int:
    item = MenuItem(store_id=store_id, name=name, unit_price=Decimal(150))
    session.add(item)
    await session.flush()
    return item.id


async def test_acquired_serialized_item_points_to_void_instead(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """收購進來的品項不走刪除：付過錢的紀錄要留著，該用的是收購作廢。"""
    mgr, _, store_id = await _seed(db_session)
    item_id = await _serialized(db_session, store_id, code="ACQ-1", acquisition=True)
    resp = await client.delete(f"/api/v1/serialized-items/{item_id}", headers=_auth(mgr))
    assert resp.status_code == 409
    assert "收購作廢" in resp.json()["detail"]
    # 被擋下時 router 會 rollback，測試共用同一個 session，連前面塞的測資都會被捲掉——
    # 所以這裡不再查「東西還在不在」，409 本身就代表沒刪。


async def test_unsold_items_are_really_deleted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """誤建的品項要從清單消失得乾乾淨淨，連入庫流水一起清掉。"""
    mgr, _, store_id = await _seed(db_session)
    item_id = await _serialized(db_session, store_id, code="DEL-1")
    product_id = await _catalog(db_session, store_id, sku="DEL-SKU")
    lot_id = await _bulk(db_session, store_id, code="DEL-LOT")
    menu_id = await _menu(db_session, store_id, name="誤建的拿鐵")

    for path in (
        f"/api/v1/serialized-items/{item_id}",
        f"/api/v1/catalog-products/{product_id}",
        f"/api/v1/bulk-lots/{lot_id}",
        f"/api/v1/menu-items/{menu_id}/delete",
    ):
        resp = await client.delete(path, headers=_auth(mgr))
        assert resp.status_code == 204, (path, resp.text)

    assert await db_session.get(SerializedItem, item_id) is None
    assert await db_session.get(CatalogProduct, product_id) is None
    assert await db_session.get(BulkLot, lot_id) is None
    assert await db_session.get(MenuItem, menu_id) is None
    movements = await db_session.scalar(
        select(func.count())
        .select_from(StockMovement)
        .where(StockMovement.serialized_item_id == item_id)
    )
    assert movements == 0  # 帳上不留指向已刪商品的孤兒列


async def test_sold_items_cannot_be_deleted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """賣過的硬刪會讓交易紀錄與毛利報表斷鏈：擋下並說明只能下架。"""
    mgr, clerk, store_id = await _seed(db_session)
    item_id = await _serialized(db_session, store_id, code="SOLD-1")
    product_id = await _catalog(db_session, store_id, sku="SOLD-SKU")
    menu_id = await _menu(db_session, store_id, name="賣過的拿鐵")

    sale = await client.post(
        "/api/v1/sales",
        json={
            "service_mode": "TAKEOUT",
            "lines": [
                {"line_type": "SERIALIZED", "item_code": "SOLD-1"},
                {"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 1},
                {"line_type": "MENU", "menu_item_id": menu_id, "qty": 1},
            ],
        },
        headers=_auth(clerk, "sold-1"),
    )
    assert sale.status_code == 201, sale.text

    for path in (
        f"/api/v1/serialized-items/{item_id}",
        f"/api/v1/catalog-products/{product_id}",
        f"/api/v1/menu-items/{menu_id}/delete",
    ):
        resp = await client.delete(path, headers=_auth(mgr))
        assert resp.status_code == 409, (path, resp.text)
        assert "賣過" in resp.json()["detail"]


async def test_delete_is_audited(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """刪除是敏感操作：留下誰刪了什麼（刪掉之後就沒別的地方查得到了）。"""
    from app.core.audit import AuditLog

    mgr, _, store_id = await _seed(db_session)
    product_id = await _catalog(db_session, store_id, sku="AUDIT-SKU")
    resp = await client.delete(f"/api/v1/catalog-products/{product_id}", headers=_auth(mgr))
    assert resp.status_code == 204, resp.text

    log = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "DELETE_CATALOG_PRODUCT")
    )
    assert log is not None
    assert log.before["sku"] == "AUDIT-SKU"
    assert log.before["name"] == "誤建的瓦斯罐"


async def test_clerk_cannot_delete(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """刪除只給管理者：店員誤按不該讓商品消失。"""
    _, clerk, store_id = await _seed(db_session)
    product_id = await _catalog(db_session, store_id, sku="RBAC-SKU")
    resp = await client.delete(f"/api/v1/catalog-products/{product_id}", headers=_auth(clerk))
    assert resp.status_code == 403


async def test_other_store_item_is_not_found(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _, _store_id = await _seed(db_session)
    other = Store(name="別家店")
    db_session.add(other)
    await db_session.flush()
    product_id = await _catalog(db_session, other.id, sku="OTHER-SKU")
    resp = await client.delete(f"/api/v1/catalog-products/{product_id}", headers=_auth(mgr))
    assert resp.status_code == 404  # 別家店的商品對本店而言就是不存在


async def test_purchased_catalog_product_cannot_be_deleted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """沒賣過但已被採購收貨：進項帳指著它，一樣不能刪。"""
    mgr, _, store_id = await _seed(db_session)
    product_id = await _catalog(db_session, store_id, sku="PO-SKU")
    supplier = await client.post(
        "/api/v1/suppliers", json={"name": "供應商"}, headers=_auth(mgr, "sup-1")
    )
    assert supplier.status_code == 201, supplier.text
    po = await client.post(
        "/api/v1/purchase-orders",
        json={
            "supplier_id": supplier.json()["id"],
            "submit": True,
            "lines": [{"catalog_product_id": product_id, "qty": 2, "unit_cost": "60"}],
        },
        headers=_auth(mgr, "po-1"),
    )
    assert po.status_code == 201, po.text

    resp = await client.delete(f"/api/v1/catalog-products/{product_id}", headers=_auth(mgr))
    assert resp.status_code == 409


async def _pending_cart(
    session: AsyncSession, store_id: int, payload: dict[str, object]
) -> None:
    """造一張「已扣款、結果不明、等著補單」的購物車，內含保存的原始結帳請求。"""
    from sqlalchemy import text

    from tests.integration.customer_display_helpers import ensure_paired_customer_display

    actor_id = await session.scalar(select(User.id).where(User.store_id == store_id))
    terminal, device = await ensure_paired_customer_display(
        session, store_id=store_id, actor_user_id=actor_id
    )
    await session.execute(
        text(
            "INSERT INTO cart_sessions (store_id, pos_terminal_id, kiosk_device_id, status,"
            " revision, snapshot, snapshot_fingerprint, payment_checkout_payload,"
            " created_at, updated_at)"
            " VALUES (:s, :t, :d, 'PAYMENT_UNCERTAIN', 1, '{}'::jsonb, 'fp',"
            " CAST(:p AS jsonb), now(), now())"
        ),
        {"s": store_id, "t": terminal.id, "d": device.id, "p": json.dumps(payload)},
    )
    await session.flush()


async def test_pending_payment_blocks_delete(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """LINE Pay 結果不明、等著補單的購物車指名了這件商品：刪掉的話那張單永遠補不出來。

    那份快照是 JSON，沒有外鍵擋得住（Codex 審查 P1），只能在刪除前自己問一次。
    """
    mgr, _, store_id = await _seed(db_session)
    product_id = await _catalog(db_session, store_id, sku="PENDING-SKU")
    menu_id = await _menu(db_session, store_id, name="待補單的拿鐵")
    payload = {
        "lines": [
            {"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 1},
            {"line_type": "MENU", "menu_item_id": menu_id, "qty": 1},
        ]
    }
    await _pending_cart(db_session, store_id, payload)

    # 一次只驗一個端點：被擋下時 router 會 rollback，測試共用同一個 session，
    # 連前面塞的測資（含這張待補單的購物車）都會跟著被捲掉。
    resp = await client.delete(f"/api/v1/catalog-products/{product_id}", headers=_auth(mgr))
    assert resp.status_code == 409, resp.text
    assert "待確認付款" in resp.json()["detail"]
    del menu_id


async def test_pending_payment_blocks_menu_delete(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """餐飲同理：待補單的那張單指名它，補完之前不能刪。"""
    mgr, _, store_id = await _seed(db_session)
    menu_id = await _menu(db_session, store_id, name="待補單的拿鐵")
    await _pending_cart(
        db_session, store_id, {"lines": [{"line_type": "MENU", "menu_item_id": menu_id, "qty": 1}]}
    )

    resp = await client.delete(f"/api/v1/menu-items/{menu_id}/delete", headers=_auth(mgr))
    assert resp.status_code == 409, resp.text
    assert "待確認付款" in resp.json()["detail"]


async def test_every_delete_endpoint_is_manager_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """四個端點一律 MANAGER-only（Codex 建議：原本只驗了一般商品）。

    403 由權限相依在進 service 之前擋下，不會 rollback，所以四個可以連著驗。
    """
    _, clerk, store_id = await _seed(db_session)
    paths = (
        f"/api/v1/serialized-items/{await _serialized(db_session, store_id, code='RB-1')}",
        f"/api/v1/catalog-products/{await _catalog(db_session, store_id, sku='RB-SKU')}",
        f"/api/v1/bulk-lots/{await _bulk(db_session, store_id, code='RB-LOT')}",
        f"/api/v1/menu-items/{await _menu(db_session, store_id, name='RB-拿鐵')}/delete",
    )
    for path in paths:
        assert (await client.delete(path, headers=_auth(clerk))).status_code == 403, path
