"""sales idempotency 併發（D-2）：同一 key 兩筆並行 POST，只建一筆、回同一單。

用真並行（asyncio.gather）兩個獨立交易的 HTTP 請求（不覆寫 get_session，各自取真 session），
驗證 (store_id, idempotency_key) 唯一約束 + router 撞約束回原單的處理：網路重試/重複提交
不會重複建單、重複扣庫存、重複收錢。
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
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import CatalogProduct, StockMovement
from app.modules.sales.models import Sale, SaleLine
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole


@pytest_asyncio.fixture
async def real_client() -> AsyncGenerator[httpx.AsyncClient]:
    """不覆寫 get_session：每個請求取真 session（真交易），供併發測試。"""
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_concurrent_same_key_creates_one_sale(real_client: httpx.AsyncClient) -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="冪等併發店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="idem-clk", password_hash="h", role=UserRole.CLERK)
        s.add(clerk)
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("1000"))
        cat = CatalogProduct(
            store_id=store.id,
            sku="SKU",
            name="飲料",
            unit_price=Decimal("100"),
            quantity_on_hand=10,
        )
        s.add(cat)
        await s.flush()
        store_id, cat_id = store.id, cat.id
        token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
        await s.commit()

    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "same-key"}
    payload = {"lines": [{"line_type": "CATALOG", "catalog_product_id": cat_id, "qty": 3}]}

    try:
        r1, r2 = await asyncio.gather(
            real_client.post("/api/v1/sales", json=payload, headers=headers),
            real_client.post("/api/v1/sales", json=payload, headers=headers),
            return_exceptions=False,
        )
        assert r1.status_code in (200, 201)
        assert r2.status_code in (200, 201)
        assert r1.json()["id"] == r2.json()["id"]  # 兩次回同一單

        async with sm() as s:
            sale_count = await s.scalar(
                select(func.count()).select_from(Sale).where(Sale.store_id == store_id)
            )
            assert sale_count == 1  # 只建一筆
            cat_after = await s.get(CatalogProduct, cat_id)
            assert cat_after is not None and cat_after.quantity_on_hand == 7  # 只扣一次
            sale_in = await s.scalar(
                select(func.count())
                .select_from(CashMovement)
                .where(CashMovement.store_id == store_id)
            )
            assert sale_in == 1  # 只收一次現
    finally:
        await _cleanup(sm, store_id)


async def test_concurrent_same_key_different_cart_one_409(real_client: httpx.AsyncClient) -> None:
    """同 key 但不同購物車併發 → 只一筆成功、另一筆 409，不得靜默丟單。"""
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="冪等衝突店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="c2", password_hash="h", role=UserRole.CLERK)
        s.add(clerk)
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("1000"))
        cat = CatalogProduct(
            store_id=store.id, sku="S", name="飲料", unit_price=Decimal("100"), quantity_on_hand=20
        )
        s.add(cat)
        await s.flush()
        store_id, cat_id = store.id, cat.id
        token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
        await s.commit()

    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "conflict-key"}
    try:
        r1, r2 = await asyncio.gather(
            real_client.post(
                "/api/v1/sales",
                json={"lines": [{"line_type": "CATALOG", "catalog_product_id": cat_id, "qty": 1}]},
                headers=headers,
            ),
            real_client.post(
                "/api/v1/sales",
                json={"lines": [{"line_type": "CATALOG", "catalog_product_id": cat_id, "qty": 2}]},
                headers=headers,
            ),
        )
        codes = sorted([r1.status_code, r2.status_code])
        assert codes == [201, 409]  # 一筆成功、不同購物車那筆被擋（非靜默成功）
        async with sm() as s:
            sale_count = await s.scalar(
                select(func.count()).select_from(Sale).where(Sale.store_id == store_id)
            )
            assert sale_count == 1
    finally:
        await _cleanup(sm, store_id)


async def _cleanup(sm: async_sessionmaker[AsyncSession], store_id: int) -> None:
    async with sm() as s:
        await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
        await s.execute(delete(SaleLine).where(SaleLine.store_id == store_id))
        await s.execute(delete(StockMovement).where(StockMovement.store_id == store_id))
        await s.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
        await s.execute(delete(Sale).where(Sale.store_id == store_id))
        await s.execute(delete(CatalogProduct).where(CatalogProduct.store_id == store_id))
        await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
        await s.execute(delete(User).where(User.store_id == store_id))
        await s.execute(delete(Store).where(Store.id == store_id))
        await s.commit()
