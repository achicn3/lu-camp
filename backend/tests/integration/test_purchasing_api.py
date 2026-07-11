"""採購/補貨 API 整合測試：supplier → purchase order → receive → catalog 入庫。"""

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
from app.shared.enums import ItemKind, StockDirection, StockReason, UserRole


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


async def _seed_store(session: AsyncSession, *, name: str = "採購店") -> tuple[str, int, int]:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    clerk = User(
        store_id=store.id, username=f"clerk-{store.id}", password_hash="h", role=UserRole.CLERK
    )
    session.add(clerk)
    await session.flush()
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, store.id, clerk.id


async def _seed_catalog(
    session: AsyncSession,
    store_id: int,
    *,
    sku: str = "CAT-1",
    qty: int = 2,
    reorder_point: int = 5,
) -> int:
    product = CatalogProduct(
        store_id=store_id,
        sku=sku,
        name="瓦斯罐",
        unit_price=Decimal("180"),
        quantity_on_hand=qty,
        reorder_point=reorder_point,
    )
    session.add(product)
    await session.flush()
    return product.id


async def _create_supplier(
    client: httpx.AsyncClient, token: str, *, name: str = "補貨供應商"
) -> int:
    resp = await client.post(
        "/api/v1/suppliers",
        json={"name": name, "contact": "sales@example.test", "tax_id": "12345678"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _create_po(
    client: httpx.AsyncClient,
    token: str,
    *,
    supplier_id: int,
    catalog_product_id: int,
    qty: int = 10,
    unit_cost: str = "120",
) -> int:
    resp = await client.post(
        "/api/v1/purchase-orders",
        json={
            "supplier_id": supplier_id,
            "lines": [
                {
                    "catalog_product_id": catalog_product_id,
                    "qty": qty,
                    "unit_cost": unit_cost,
                }
            ],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "ORDERED"
    assert body["total_cost"] == str(Decimal(qty) * Decimal(unit_cost))
    return int(body["id"])


async def test_receive_purchase_order_replenishes_catalog_and_records_stock_movement(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _clerk_id = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(
        client,
        token,
        supplier_id=supplier_id,
        catalog_product_id=catalog_id,
        qty=10,
        unit_cost="120",
    )

    received = await client.post(f"/api/v1/purchase-orders/{po_id}/receive", headers=_auth(token))

    assert received.status_code == 200, received.text
    body = received.json()
    assert body["purchase_order"]["status"] == "RECEIVED"
    assert body["receipt_id"] is not None
    product = await db_session.get(CatalogProduct, catalog_id)
    assert product is not None
    assert product.quantity_on_hand == 12

    movements = (
        await db_session.scalars(
            select(StockMovement).where(
                StockMovement.store_id == store_id,
                StockMovement.item_kind == ItemKind.CATALOG,
                StockMovement.catalog_product_id == catalog_id,
                StockMovement.direction == StockDirection.IN,
                StockMovement.reason == StockReason.PURCHASE,
                StockMovement.ref_type == "purchase_order",
                StockMovement.ref_id == po_id,
            )
        )
    ).all()
    assert len(movements) == 1
    assert movements[0].qty == 10

    low_stock = await client.get(
        "/api/v1/catalog-products", params={"low_stock": "true"}, headers=_auth(token)
    )
    assert low_stock.status_code == 200, low_stock.text
    assert all(row["id"] != catalog_id for row in low_stock.json())


async def test_receive_purchase_order_twice_returns_409_without_double_stock(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _clerk_id = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=catalog_id)

    first = await client.post(f"/api/v1/purchase-orders/{po_id}/receive", headers=_auth(token))
    second = await client.post(f"/api/v1/purchase-orders/{po_id}/receive", headers=_auth(token))

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    product = await db_session.get(CatalogProduct, catalog_id)
    assert product is not None
    assert product.quantity_on_hand == 12
    movement_count = await db_session.scalar(
        select(func.count())
        .select_from(StockMovement)
        .where(
            StockMovement.store_id == store_id,
            StockMovement.reason == StockReason.PURCHASE,
            StockMovement.ref_type == "purchase_order",
            StockMovement.ref_id == po_id,
        )
    )
    assert movement_count == 1


async def test_create_purchase_order_rejects_cross_store_catalog_product(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token_a, _store_a, _clerk_a = await _seed_store(db_session, name="A 店")
    _token_b, store_b, _clerk_b = await _seed_store(db_session, name="B 店")
    catalog_b = await _seed_catalog(db_session, store_b, sku="B-CAT")
    supplier_a = await _create_supplier(client, token_a, name="A 供應商")

    resp = await client.post(
        "/api/v1/purchase-orders",
        json={
            "supplier_id": supplier_a,
            "lines": [{"catalog_product_id": catalog_b, "qty": 3, "unit_cost": "100"}],
        },
        headers=_auth(token_a),
    )

    assert resp.status_code == 422, resp.text


async def test_create_purchase_order_rejects_cross_store_supplier(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token_a, store_a, _clerk_a = await _seed_store(db_session, name="A 店")
    token_b, _store_b, _clerk_b = await _seed_store(db_session, name="B 店")
    catalog_a = await _seed_catalog(db_session, store_a, sku="A-CAT")
    supplier_b = await _create_supplier(client, token_b, name="B 供應商")

    resp = await client.post(
        "/api/v1/purchase-orders",
        json={
            "supplier_id": supplier_b,
            "lines": [{"catalog_product_id": catalog_a, "qty": 3, "unit_cost": "100"}],
        },
        headers=_auth(token_a),
    )

    assert resp.status_code == 422, resp.text


async def test_create_supplier_blank_name_returns_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, _ = await _seed_store(db_session)
    resp = await client.post("/api/v1/suppliers", json={"name": "   "}, headers=_auth(token))
    assert resp.status_code == 422, resp.text


async def test_list_suppliers_returns_created(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, _ = await _seed_store(db_session)
    supplier_id = await _create_supplier(client, token, name="阿里山補給")
    resp = await client.get("/api/v1/suppliers", headers=_auth(token))
    assert resp.status_code == 200
    assert any(s["id"] == supplier_id for s in resp.json())


async def test_create_po_duplicate_product_returns_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session)
    supplier_id = await _create_supplier(client, token)
    cat_id = await _seed_catalog(db_session, store_id)
    resp = await client.post(
        "/api/v1/purchase-orders",
        json={
            "supplier_id": supplier_id,
            "lines": [
                {"catalog_product_id": cat_id, "qty": 5, "unit_cost": "100"},
                {"catalog_product_id": cat_id, "qty": 3, "unit_cost": "100"},
            ],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_list_and_get_purchase_order(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, _ = await _seed_store(db_session)
    supplier_id = await _create_supplier(client, token)
    cat_id = await _seed_catalog(db_session, store_id)
    po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=cat_id)

    listed = await client.get("/api/v1/purchase-orders", headers=_auth(token))
    assert listed.status_code == 200
    assert any(po["id"] == po_id for po in listed.json())

    got = await client.get(f"/api/v1/purchase-orders/{po_id}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["id"] == po_id

    missing = await client.get("/api/v1/purchase-orders/999999", headers=_auth(token))
    assert missing.status_code == 404


async def test_receive_unknown_purchase_order_returns_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, _ = await _seed_store(db_session)
    resp = await client.post("/api/v1/purchase-orders/999999/receive", headers=_auth(token))
    assert resp.status_code == 404, resp.text


async def test_list_purchase_orders_filters_by_status_and_paginates(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """採購單清單可依狀態篩選、並支援 limit/offset 分頁。"""
    token, store_id, _ = await _seed_store(db_session)
    catalog_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_open = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=1, unit_cost="10"
    )
    po_recv = await _create_po(
        client, token, supplier_id=supplier_id, catalog_product_id=catalog_id, qty=2, unit_cost="20"
    )
    await client.post(f"/api/v1/purchase-orders/{po_recv}/receive", headers=_auth(token))

    ordered = await client.get(
        "/api/v1/purchase-orders", params={"status": "ORDERED"}, headers=_auth(token)
    )
    assert ordered.status_code == 200, ordered.text
    ordered_ids = [po["id"] for po in ordered.json()]
    assert po_open in ordered_ids and po_recv not in ordered_ids

    received = await client.get(
        "/api/v1/purchase-orders", params={"status": "RECEIVED"}, headers=_auth(token)
    )
    received_ids = [po["id"] for po in received.json()]
    assert po_recv in received_ids and po_open not in received_ids

    # 分頁：limit=1 各頁一筆、不重疊。
    page0 = await client.get(
        "/api/v1/purchase-orders", params={"limit": 1, "offset": 0}, headers=_auth(token)
    )
    page1 = await client.get(
        "/api/v1/purchase-orders", params={"limit": 1, "offset": 1}, headers=_auth(token)
    )
    assert len(page0.json()) == 1 and len(page1.json()) == 1
    assert page0.json()[0]["id"] != page1.json()[0]["id"]


# ── 進項發票（裁示 2026-07-11：收貨時登錄；漏登可補登一次）──────────────────


async def test_receive_with_input_invoice_stores_and_splits_tax(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """收貨帶進項發票 → 落庫且稅額拆分（total 1050、5% → net 1000/tax 50）；列表可見。"""
    token, store_id, _clerk_id = await _seed_store(db_session)
    product_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=product_id)

    received = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receive",
        json={
            "invoice": {
                "invoice_number": "AB12345678",
                "invoice_date": "2026-07-11",
                "invoice_total": "1050",
            }
        },
        headers=_auth(token),
    )
    assert received.status_code == 200, received.text
    invoice = received.json()["purchase_order"]["invoice"]
    assert invoice == {
        "invoice_number": "AB12345678",
        "invoice_date": "2026-07-11",
        "invoice_total": "1050",
        "invoice_net": "1000",
        "invoice_tax": "50",
    }


async def test_receive_without_invoice_then_backfill_once(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """收貨未帶發票 → invoice=None；補登一次成功；再補登 → 409（不可覆寫）。"""
    token, store_id, _clerk_id = await _seed_store(db_session)
    product_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=product_id)

    received = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receive", headers=_auth(token)
    )
    assert received.status_code == 200, received.text
    assert received.json()["purchase_order"]["invoice"] is None

    backfill = await client.post(
        f"/api/v1/purchase-orders/{po_id}/invoice",
        json={
            "invoice_number": "CD98765432",
            "invoice_date": "2026-07-11",
            "invoice_total": "2100",
        },
        headers=_auth(token),
    )
    assert backfill.status_code == 200, backfill.text
    assert backfill.json()["invoice_net"] == "2000"
    assert backfill.json()["invoice_tax"] == "100"

    again = await client.post(
        f"/api/v1/purchase-orders/{po_id}/invoice",
        json={
            "invoice_number": "EF11111111",
            "invoice_date": "2026-07-11",
            "invoice_total": "999",
        },
        headers=_auth(token),
    )
    assert again.status_code == 409, again.text


async def test_backfill_requires_received_order(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """未收貨的採購單不可補登發票 → 409。"""
    token, store_id, _clerk_id = await _seed_store(db_session)
    product_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=product_id)

    resp = await client.post(
        f"/api/v1/purchase-orders/{po_id}/invoice",
        json={
            "invoice_number": "GH22222222",
            "invoice_date": "2026-07-11",
            "invoice_total": "500",
        },
        headers=_auth(token),
    )
    assert resp.status_code == 409, resp.text


async def test_invoice_number_format_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """號碼非 2 英文＋8 數字 → 422（pydantic pattern）。"""
    token, store_id, _clerk_id = await _seed_store(db_session)
    product_id = await _seed_catalog(db_session, store_id)
    supplier_id = await _create_supplier(client, token)
    po_id = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=product_id)
    resp = await client.post(
        f"/api/v1/purchase-orders/{po_id}/receive",
        json={
            "invoice": {
                "invoice_number": "1234567890",
                "invoice_date": "2026-07-11",
                "invoice_total": "1050",
            }
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_duplicate_invoice_number_rejected_across_pos(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同店同號同日的實體發票不可重複入帳（收貨與補登皆擋 409；Codex 第一輪 high）。"""
    token, store_id, _clerk_id = await _seed_store(db_session)
    p1 = await _seed_catalog(db_session, store_id, sku="DUP-1")
    p2 = await _seed_catalog(db_session, store_id, sku="DUP-2")
    supplier_id = await _create_supplier(client, token)
    po1 = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=p1)
    po2 = await _create_po(client, token, supplier_id=supplier_id, catalog_product_id=p2)
    invoice = {
        "invoice_number": "ZZ55667788",
        "invoice_date": "2026-07-11",
        "invoice_total": "1050",
    }
    first = await client.post(
        f"/api/v1/purchase-orders/{po1}/receive", json={"invoice": invoice}, headers=_auth(token)
    )
    assert first.status_code == 200, first.text
    # 收貨路徑重複 → 409
    dup_receive = await client.post(
        f"/api/v1/purchase-orders/{po2}/receive", json={"invoice": invoice}, headers=_auth(token)
    )
    assert dup_receive.status_code == 409, dup_receive.text
    # po2 未收貨成功（原子回滾）：再收一次（無發票）→ 200，補登同號 → 409
    ok2 = await client.post(f"/api/v1/purchase-orders/{po2}/receive", headers=_auth(token))
    assert ok2.status_code == 200, ok2.text
    dup_backfill = await client.post(
        f"/api/v1/purchase-orders/{po2}/invoice", json=invoice, headers=_auth(token)
    )
    assert dup_backfill.status_code == 409, dup_backfill.text
    # 不同日期＝不同期別回收字軌 → 允許
    other_date = {**invoice, "invoice_date": "2026-09-11"}
    ok3 = await client.post(
        f"/api/v1/purchase-orders/{po2}/invoice", json=other_date, headers=_auth(token)
    )
    assert ok3.status_code == 200, ok3.text
