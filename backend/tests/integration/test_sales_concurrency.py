"""sales 併發不變量：同一序號品兩筆並行結帳，只有一筆成功。

需真正的兩條交易並行，故用獨立 session（各自 commit），由序號品狀態機的條件式 UPDATE
（IN_STOCK→SOLD）擋下重複售出，而非先查再改。結束在 finally 清掉自建列。
"""

import asyncio
from decimal import Decimal

from sqlalchemy import delete, func, select

import app.core.db as app_db
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import SerializedItem, StockMovement
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import Sale, SaleLine
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import Grade, OwnershipType, SaleLineType, SerializedItemStatus, UserRole
from app.shared.exceptions import DomainError


async def test_concurrent_sale_of_same_serialized_only_one_succeeds() -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發結帳店")
        s.add(store)
        await s.flush()
        clerk = User(
            store_id=store.id, username="conc-sale", password_hash="h", role=UserRole.CLERK
        )
        s.add(clerk)
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("1000"))
        await InventoryService(s).create_serialized_item(
            store.id,
            item_code="CONC-S1",
            name="限量品",
            grade=Grade.A,
            ownership_type=OwnershipType.OWNED,
            listed_price=Decimal("3000"),
            acquisition_cost=Decimal("1800"),
        )
        store_id, clerk_id = store.id, clerk.id
        await s.commit()

    try:

        async def sell() -> bool:
            async with sm() as s:
                try:
                    await SalesService(s).create_sale(
                        store_id,
                        clerk_id,
                        lines=[
                            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="CONC-S1")
                        ],
                    )
                    await s.commit()
                    return True
                except DomainError:
                    await s.rollback()
                    return False

        results = await asyncio.gather(sell(), sell())
        assert sum(results) == 1  # 只有一筆成功

        async with sm() as s:
            ser = await s.scalar(select(SerializedItem).where(SerializedItem.store_id == store_id))
            assert ser is not None and ser.status == SerializedItemStatus.SOLD
            # 恰好一筆 sale、一筆 OUT 異動、一筆 SALE_IN。
            assert (
                await s.scalar(
                    select(func.count()).select_from(Sale).where(Sale.store_id == store_id)
                )
            ) == 1
            assert (
                await s.scalar(
                    select(func.count())
                    .select_from(StockMovement)
                    .where(StockMovement.store_id == store_id)
                )
            ) == 1
            assert (
                await s.scalar(
                    select(func.count())
                    .select_from(CashMovement)
                    .where(CashMovement.store_id == store_id)
                )
            ) == 1
    finally:
        async with sm() as s:
            for model in (SaleLine, StockMovement, CashMovement, Sale, SerializedItem, CashSession):
                await s.execute(delete(model).where(model.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()
