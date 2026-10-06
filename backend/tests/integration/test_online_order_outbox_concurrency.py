"""同一張訂單的回報不可因 SKIP LOCKED 越過仍在送出的前一筆。"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.time import utc_now
from app.modules.onlineorder.models import OnlineOrder, OnlineOrderOutbox
from app.modules.onlineorder.orders_service import OnlineOrdersService, _order_from_cloud
from app.modules.store.models import Store
from tests.conftest import test_engine
from tests.integration.test_online_orders_store import FakeWorker, _client, _line, _order, _rid


async def test_locked_predecessor_blocks_later_report_until_committed() -> None:
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions() as seed:
        store = Store(name="回報並發測試")
        seed.add(store)
        await seed.flush()
        order = _order_from_cloud(store.id, _order(_rid(901), [_line(1, 1, "拿鐵", 150)]))
        seed.add(order)
        await seed.flush()
        first = OnlineOrderOutbox(
            store_id=store.id,
            online_order_id=order.id,
            remote_id=order.remote_id,
            payload={"sync_status": "IMPORTED"},
            status="PENDING",
            attempts=0,
            next_attempt_at=utc_now(),
        )
        seed.add(first)
        await seed.commit()
    worker = FakeWorker()
    try:
        async with sessions() as sending, sessions() as later, sessions() as competing:
            await sending.scalar(
                select(OnlineOrderOutbox).where(OnlineOrderOutbox.id == first.id).with_for_update()
            )
            later.add(
                OnlineOrderOutbox(
                    store_id=store.id,
                    online_order_id=order.id,
                    remote_id=order.remote_id,
                    payload={"sync_status": "SETTLED", "payment_status": "PAID"},
                    status="PENDING",
                    attempts=0,
                    next_attempt_at=utc_now(),
                )
            )
            await later.commit()
            service = OnlineOrdersService(competing, _client(worker, store.id))
            assert await service.flush_outbox(store.id) == 0
            assert worker.reports == []
            await competing.commit()
            await sending.rollback()
            await service.flush_outbox(store.id)
            await competing.commit()
            await service.flush_outbox(store.id)
            await competing.commit()
            assert [payload["sync_status"] for _, payload in worker.reports] == [
                "IMPORTED",
                "SETTLED",
            ]
    finally:
        async with sessions() as cleanup:
            await cleanup.execute(
                delete(OnlineOrderOutbox).where(OnlineOrderOutbox.store_id == store.id)
            )
            await cleanup.execute(delete(OnlineOrder).where(OnlineOrder.store_id == store.id))
            await cleanup.execute(delete(Store).where(Store.id == store.id))
            await cleanup.commit()
