"""sales 整筆原子性：結帳中任一步失敗 → 整筆回復，不留半套（T11 最重要的測試）。

用獨立 session（真交易、各自 commit），在「庫存已扣、要收現」那一步注入失敗，證明
sales / sale_lines / stock_movements / cash_movements 全部沒落地，且序號品仍 IN_STOCK、
一般商品庫存未被扣——即不會出現「庫存扣了但現金沒進」的半套。
"""

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.db as app_db
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.inventory.models import CatalogProduct, SerializedItem, StockMovement
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import Sale, SaleLine
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import Grade, OwnershipType, SaleLineType, SerializedItemStatus, UserRole


async def _count(session: AsyncSession, model: Any, store_id: int) -> int:
    n = await session.scalar(
        select(func.count()).select_from(model).where(model.store_id == store_id)
    )
    return n or 0


async def test_sale_rolls_back_entirely_when_cash_step_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="原子結帳店")
        s.add(store)
        await s.flush()
        clerk = User(
            store_id=store.id, username="atomic-sale", password_hash="h", role=UserRole.CLERK
        )
        s.add(clerk)
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("1000"))
        inv = InventoryService(s)
        await inv.create_serialized_item(
            store.id,
            item_code="ATOM-S1",
            name="相機",
            grade=Grade.A,
            ownership_type=OwnershipType.OWNED,
            listed_price=Decimal("3000"),
            acquisition_cost=Decimal("1800"),
        )
        cat = CatalogProduct(
            store_id=store.id,
            sku="ATOM-C",
            name="飲料",
            unit_price=Decimal("150"),
            quantity_on_hand=10,
        )
        s.add(cat)
        await s.flush()
        store_id, clerk_id, cat_id = store.id, clerk.id, cat.id
        await s.commit()

    lines = [
        SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="ATOM-S1"),
        SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=cat_id, qty=2),
    ]

    async def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("模擬收現步驟失敗")

    monkeypatch.setattr(CashDrawerService, "record_movement", _boom)

    try:
        async with sm() as s:
            with pytest.raises(RuntimeError):
                await SalesService(s).create_sale(store_id, clerk_id, lines=lines)
            await s.rollback()

        async with sm() as s:
            # 全部沒落地。
            assert await _count(s, Sale, store_id) == 0
            assert await _count(s, SaleLine, store_id) == 0
            assert await _count(s, StockMovement, store_id) == 0
            assert await _count(s, CashMovement, store_id) == 0
            # 序號品仍 IN_STOCK、一般商品庫存未被扣（UPDATE 也回滾了）。
            ser = await s.scalar(select(SerializedItem).where(SerializedItem.store_id == store_id))
            assert ser is not None and ser.status == SerializedItemStatus.IN_STOCK
            cat_after = await s.scalar(
                select(CatalogProduct).where(CatalogProduct.store_id == store_id)
            )
            assert cat_after is not None and cat_after.quantity_on_hand == 10
    finally:
        async with sm() as s:
            for model in (
                SaleLine,
                StockMovement,
                CashMovement,
                ConsignmentSettlement,
                Sale,
                SerializedItem,
                CatalogProduct,
                CashSession,
            ):
                await s.execute(delete(model).where(model.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()
