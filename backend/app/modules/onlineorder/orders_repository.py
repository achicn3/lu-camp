"""線上訂單（店內端）的資料存取：訂單、份數保留、回報佇列、雲端連線狀態。"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
            stmt = stmt.with_for_update()
        row: OnlineOrder | None = await self._session.scalar(stmt)
        return row

    async def list_since(self, store_id: int, since: datetime) -> Sequence[OnlineOrder]:
        """今天的單＋還沒處理完的舊單（未付款、沒取消），新的在前。"""
        return (
            await self._session.scalars(
                select(OnlineOrder)
                .where(
                    OnlineOrder.store_id == store_id,
                    (OnlineOrder.remote_created_at >= since)
                    | (OnlineOrder.sync_status == "IMPORTED"),
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
            stmt = stmt.with_for_update()
        row: StockReservation | None = await self._session.scalar(stmt)
        return row

    async def expired_reservations(
        self, store_id: int, now: datetime
    ) -> Sequence[StockReservation]:
        return (
            await self._session.scalars(
                select(StockReservation)
                .where(
                    StockReservation.store_id == store_id,
                    StockReservation.status == StockReservationStatus.ACTIVE,
                    StockReservation.expires_at <= now,
                )
                .order_by(StockReservation.id)
                .with_for_update(skip_locked=True)
            )
        ).all()

    async def pending_outbox(self, store_id: int) -> Sequence[OnlineOrderOutbox]:
        """待送的回報，照 id 先後（同一張單要依序送）。別的程序正在送的跳過。"""
        return (
            await self._session.scalars(
                select(OnlineOrderOutbox)
                .where(
                    OnlineOrderOutbox.store_id == store_id,
                    OnlineOrderOutbox.status == OnlineOutboxStatus.PENDING,
                )
                .order_by(OnlineOrderOutbox.id)
                .with_for_update(skip_locked=True)
            )
        ).all()

    async def link(self, store_id: int) -> OnlineOrderLink | None:
        return await self._session.get(OnlineOrderLink, store_id)
