"""sales 作廢併發（Codex T12 ②）：兩個並行 void 只一個成功、稽核恰一筆。

真並行（asyncio.gather）兩個獨立交易的 void 請求（不覆寫 get_session）。void_sale 先以
SELECT … FOR UPDATE 鎖 sale 列再檢查狀態（比照 D-1），故只有一個轉 VOID 成功、另一個
→ SaleAlreadyVoid → 409，且只寫一筆 VOID_SALE 稽核。
"""

import asyncio
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import delete, func, select

import app.core.db as app_db
from app.core.audit import AuditLog
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import CatalogProduct, StockMovement
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import Sale, SaleLine
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import SaleInvoiceStatus, SaleLineType, UserRole


@pytest_asyncio.fixture
async def real_client() -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_concurrent_void_only_one_succeeds(real_client: httpx.AsyncClient) -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發作廢店")
        s.add(store)
        await s.flush()
        mgr = User(store_id=store.id, username="void-mgr", password_hash="h", role=UserRole.MANAGER)
        s.add(mgr)
        await s.flush()
        await CashDrawerService(s).open_session(store.id, mgr.id, Decimal("1000"))
        cat = CatalogProduct(
            store_id=store.id,
            sku="SKU",
            name="飲料",
            unit_price=Decimal("100"),
            quantity_on_hand=10,
        )
        s.add(cat)
        await s.flush()
        sale = await SalesService(s).create_sale(
            store.id,
            mgr.id,
            lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=cat.id, qty=1)],
        )
        store_id, sale_id = store.id, sale.id
        token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
        await s.commit()

    headers = {"Authorization": f"Bearer {token}"}
    try:
        r1, r2 = await asyncio.gather(
            real_client.post(f"/api/v1/sales/{sale_id}/void", headers=headers),
            real_client.post(f"/api/v1/sales/{sale_id}/void", headers=headers),
        )
        codes = sorted([r1.status_code, r2.status_code])
        assert codes == [200, 409]  # 恰一個成功、一個被擋

        async with sm() as s:
            voided = await s.get(Sale, sale_id)
            assert voided is not None and voided.invoice_status == SaleInvoiceStatus.VOID
            audit_count = await s.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.store_id == store_id, AuditLog.action == "VOID_SALE")
            )
            assert audit_count == 1  # 稽核恰一筆
    finally:
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
