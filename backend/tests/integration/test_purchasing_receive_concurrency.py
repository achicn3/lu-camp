"""分批收貨冪等併發：同一 Idempotency-Key 兩筆並行 POST，只入庫一次、回同一收貨批次。

用真並行（asyncio.gather）兩個獨立交易的 HTTP 請求（不覆寫 get_session，各自取真 session），
驗證 (store_id, idempotency_key) 唯一索引 + router 撞索引回放：網路重試/重複提交不重複入庫。
"""

import asyncio
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.core.db as app_db
from app.core.audit import AuditLog
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import CatalogProduct, StockMovement
from app.modules.purchasing.models import (
    GoodsReceipt,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import PurchaseOrderStatus, StockReason, UserRole


@pytest_asyncio.fixture
async def real_client() -> AsyncGenerator[httpx.AsyncClient]:
    """不覆寫 get_session：每個請求取真 session（真交易），供併發測試。"""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _setup(sm: async_sessionmaker[AsyncSession]) -> tuple[str, int, int, int, int]:
    async with sm() as s:
        store = Store(name="收貨併發店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="recv-clk", password_hash="h", role=UserRole.CLERK)
        s.add(clerk)
        await s.flush()
        supplier = Supplier(store_id=store.id, name="併發供應商")
        s.add(supplier)
        cat = CatalogProduct(
            store_id=store.id, sku="RC", name="瓦斯", unit_price=Decimal("100"), quantity_on_hand=0
        )
        s.add(cat)
        await s.flush()
        po = PurchaseOrder(
            store_id=store.id,
            supplier_id=supplier.id,
            ordered_by=clerk.id,
            status=PurchaseOrderStatus.ORDERED,
        )
        s.add(po)
        await s.flush()
        line = PurchaseOrderLine(
            store_id=store.id,
            purchase_order_id=po.id,
            catalog_product_id=cat.id,
            qty=10,
            unit_cost=Decimal("50"),
        )
        s.add(line)
        await s.flush()
        token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
        result = (token, store.id, cat.id, po.id, line.id)
        await s.commit()
    return result


async def test_concurrent_receive_same_key_stocks_once(real_client: httpx.AsyncClient) -> None:
    """同 key 兩筆並行收貨：回同一 receipt、庫存/異動/收貨批次皆只加一次。"""
    sm = app_db.get_sessionmaker()
    token, store_id, cat_id, po_id, line_id = await _setup(sm)
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "recv-same"}
    body = {"lines": [{"line_id": line_id, "qty": 3}]}
    url = f"/api/v1/purchase-orders/{po_id}/receive"
    try:
        r1, r2 = await asyncio.gather(
            real_client.post(url, json=body, headers=headers),
            real_client.post(url, json=body, headers=headers),
        )
        assert r1.status_code in (200, 201), r1.text
        assert r2.status_code in (200, 201), r2.text
        assert r1.json()["receipt_id"] == r2.json()["receipt_id"]  # 兩次回同一收貨批次

        async with sm() as s:
            cat_after = await s.get(CatalogProduct, cat_id)
            assert cat_after is not None and cat_after.quantity_on_hand == 3  # 只加一次
            receipt_count = await s.scalar(
                select(func.count())
                .select_from(GoodsReceipt)
                .where(GoodsReceipt.purchase_order_id == po_id)
            )
            assert receipt_count == 1  # 只一筆收貨批次
            movement_count = await s.scalar(
                select(func.count())
                .select_from(StockMovement)
                .where(
                    StockMovement.store_id == store_id,
                    StockMovement.catalog_product_id == cat_id,
                    StockMovement.reason == StockReason.PURCHASE,
                )
            )
            assert movement_count == 1  # 只一筆庫存異動
            line_after = await s.get(PurchaseOrderLine, line_id)
            assert line_after is not None and line_after.received_qty == 3  # 只累加一次
    finally:
        await _cleanup(sm, store_id)


async def _cleanup(sm: async_sessionmaker[AsyncSession], store_id: int) -> None:
    async with sm() as s:
        await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
        await s.execute(delete(StockMovement).where(StockMovement.store_id == store_id))
        await s.execute(delete(GoodsReceipt).where(GoodsReceipt.store_id == store_id))
        await s.execute(delete(PurchaseOrderLine).where(PurchaseOrderLine.store_id == store_id))
        await s.execute(delete(PurchaseOrder).where(PurchaseOrder.store_id == store_id))
        await s.execute(delete(Supplier).where(Supplier.store_id == store_id))
        await s.execute(delete(CatalogProduct).where(CatalogProduct.store_id == store_id))
        await s.execute(delete(User).where(User.store_id == store_id))
        await s.execute(delete(Store).where(Store.id == store_id))
        await s.commit()
