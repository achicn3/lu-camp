"""線上訂單（店內端）的資料存取：訂單、份數保留、回報佇列、雲端連線狀態。"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.onlineorder.models import (
    OnlineOrder,
    OnlineOrderLink,
    OnlineOrderOutbox,
    StockReservation,
)
from app.shared.enums import OnlineOutboxStatus, StockReservationStatus


class OnlineOrdersRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(
        self, row: OnlineOrder | StockReservation | OnlineOrderOutbox | OnlineOrderLink
    ) -> None:
        self._session.add(row)

    async def flush(self) -> None:
        await self._session.flush()

    async def by_remote_id(self, store_id: int, remote_id: str) -> OnlineOrder | None:
        row: OnlineOrder | None = await self._session.scalar(
            select(OnlineOrder).where(
                OnlineOrder.store_id == store_id, OnlineOrder.remote_id == remote_id
            )
        )
        return row

    async def get(
        self, store_id: int, order_id: int, *, for_update: bool = False
    ) -> OnlineOrder | None:
        stmt = select(OnlineOrder).where(
            OnlineOrder.store_id == store_id, OnlineOrder.id == order_id
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        row: OnlineOrder | None = await self._session.scalar(stmt)
        return row

    async def by_sale(self, store_id: int, sale_id: int) -> OnlineOrder | None:
        """成立這筆銷售的線上單（鎖住，作廢時同一交易內改它）。"""
        row: OnlineOrder | None = await self._session.scalar(
            select(OnlineOrder)
            .where(OnlineOrder.store_id == store_id, OnlineOrder.sale_id == sale_id)
            .with_for_update()
        )
        return row

    async def list_since(self, store_id: int, since: datetime) -> Sequence[OnlineOrder]:
        """今天的單＋還沒處理完的舊單（未付款、沒取消，或還沒交貨），新的在前。"""
        return (
            await self._session.scalars(
                select(OnlineOrder)
                .where(
                    OnlineOrder.store_id == store_id,
                    (OnlineOrder.remote_created_at >= since)
                    | (OnlineOrder.sync_status == "IMPORTED")
                    # 付了錢、帶回家商品還沒交給客人的舊單也要留在清單上（docs/63 §13）。
                    | (OnlineOrder.fulfillment_status == "AWAITING"),
                )
                .order_by(OnlineOrder.remote_created_at.desc(), OnlineOrder.id.desc())
            )
        ).all()

    async def reservation(
        self, store_id: int, order_id: int, *, for_update: bool = False
    ) -> StockReservation | None:
        stmt = select(StockReservation).where(
            StockReservation.store_id == store_id, StockReservation.online_order_id == order_id
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        row: StockReservation | None = await self._session.scalar(stmt)
        return row

    async def expired_orders(self, store_id: int, now: datetime) -> Sequence[OnlineOrder]:
        # 與結帳／取消一致：先鎖訂單，再鎖保留，避免互相等待。
        return (
            await self._session.scalars(
                select(OnlineOrder)
                .join(StockReservation, StockReservation.online_order_id == OnlineOrder.id)
                .where(
                    OnlineOrder.store_id == store_id,
                    StockReservation.status == StockReservationStatus.ACTIVE,
                    StockReservation.expires_at <= now,
                )
                .order_by(OnlineOrder.id)
                .with_for_update(of=OnlineOrder, skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).all()

    async def pending_outbox(self, store_id: int) -> Sequence[OnlineOrderOutbox]:
        """每張單只取最早待送的一筆；前筆被其他程序鎖住時不可越過它。"""
        earlier = aliased(OnlineOrderOutbox)
        predecessor = (
            select(earlier.id)
            .where(
                earlier.store_id == OnlineOrderOutbox.store_id,
                earlier.online_order_id == OnlineOrderOutbox.online_order_id,
                earlier.id < OnlineOrderOutbox.id,
                earlier.status == OnlineOutboxStatus.PENDING,
            )
            .exists()
        )
        return (
            await self._session.scalars(
                select(OnlineOrderOutbox)
                .where(
                    OnlineOrderOutbox.store_id == store_id,
                    OnlineOrderOutbox.status == OnlineOutboxStatus.PENDING,
                    ~predecessor,
                )
                .order_by(OnlineOrderOutbox.id)
                .with_for_update(skip_locked=True)
            )
        ).all()

    async def link(self, store_id: int) -> OnlineOrderLink | None:
        return await self._session.get(OnlineOrderLink, store_id)
