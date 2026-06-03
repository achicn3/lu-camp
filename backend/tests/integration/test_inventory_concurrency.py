"""inventory 併發不變量（必測 2、3 的併發部分）。

需要真正的兩條交易並行，故用獨立 session（各自 commit），不走 db_session 回滾隔離；
測試結束在 finally 清掉自建的列，不留殘餘。
"""

import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal

from sqlalchemy import delete

import app.core.db as app_db
from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.inventory.service import InventoryService
from app.modules.store.models import Store
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    Grade,
    OwnershipType,
    SerializedItemStatus,
)
from app.shared.exceptions import InsufficientStock, InvalidStateTransition


async def _run_twice(op: Callable[[], Awaitable[bool]]) -> int:
    """並行跑兩次，回傳成功次數。"""
    results = await asyncio.gather(op(), op())
    return sum(results)


async def test_concurrent_sell_serialized_only_one_succeeds() -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發店")
        s.add(store)
        await s.flush()
        item = SerializedItem(
            store_id=store.id,
            item_code="CONC-SER-1",
            name="併發品",
            grade=Grade.A,
            ownership_type=OwnershipType.OWNED,
            acquisition_cost=Decimal("100"),
            listed_price=Decimal("300"),
        )
        s.add(item)
        await s.flush()
        store_id, item_id = store.id, item.id
        await s.commit()

    try:

        async def sell() -> bool:
            async with sm() as s:
                try:
                    await InventoryService(s).sell_serialized_item(item_id)
                    await s.commit()
                    return True
                except InvalidStateTransition:
                    await s.rollback()
                    return False

        assert await _run_twice(sell) == 1

        async with sm() as s:
            fetched = await s.get(SerializedItem, item_id)
            assert fetched is not None
            assert fetched.status == SerializedItemStatus.SOLD
    finally:
        async with sm() as s:
            await s.execute(delete(SerializedItem).where(SerializedItem.id == item_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()


async def test_concurrent_bulk_oversell_blocked() -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發店2")
        s.add(store)
        await s.flush()
        lot = BulkLot(
            store_id=store.id,
            lot_code="CONC-LOT-1",
            name="併發散裝",
            grade=Grade.E,
            acquisition_cost=Decimal("1000"),
            acquisition_basis=BulkAcquisitionBasis.WEIGHT,
            unit_price=Decimal("50"),
            total_qty=5,
            remaining_qty=5,
        )
        s.add(lot)
        await s.flush()
        store_id, lot_id = store.id, lot.id
        await s.commit()

    try:
        # 兩筆各賣 3（共 6 > 5）→ 只能成功一筆。
        async def take() -> bool:
            async with sm() as s:
                try:
                    await InventoryService(s).sell_bulk_lot_items(lot_id, 3)
                    await s.commit()
                    return True
                except InsufficientStock:
                    await s.rollback()
                    return False

        assert await _run_twice(take) == 1

        async with sm() as s:
            fetched = await s.get(BulkLot, lot_id)
            assert fetched is not None
            assert fetched.remaining_qty == 2  # 未超賣、未變負
            assert fetched.status == BulkLotStatus.ON_SALE
    finally:
        async with sm() as s:
            await s.execute(delete(BulkLot).where(BulkLot.id == lot_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()
