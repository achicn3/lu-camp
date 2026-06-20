"""cashdrawer 資料存取層（唯一直接碰 ORM 的層）。"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.models import CashMovement, CashSession
from app.shared.enums import CashMovementType, CashSessionStatus


class CashDrawerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_session(self, cash_session: CashSession) -> CashSession:
        self._session.add(cash_session)
        await self._session.flush()
        return cash_session

    async def get_open_session(
        self, store_id: int, *, for_update: bool = False
    ) -> CashSession | None:
        """取開帳中的 session。

        for_update=True 時對該列上 row lock（SELECT … FOR UPDATE），供寫入現金異動前序列化，
        與關帳互斥；唯讀的前置檢查（如 get_current_session）用預設不上鎖。
        """
        stmt = select(CashSession).where(
            CashSession.store_id == store_id,
            CashSession.status == CashSessionStatus.OPEN,
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result: CashSession | None = await self._session.scalar(stmt)
        return result

    async def lock_session(self, store_id: int, session_id: int) -> CashSession | None:
        """對指定 session 列上 row lock 並刷新到已提交狀態（供關帳前序列化、與現金異動互斥）。"""
        stmt = (
            select(CashSession)
            .where(CashSession.id == session_id, CashSession.store_id == store_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result: CashSession | None = await self._session.scalar(stmt)
        return result

    async def get_session(self, store_id: int, session_id: int) -> CashSession | None:
        stmt = select(CashSession).where(
            CashSession.id == session_id, CashSession.store_id == store_id
        )
        result: CashSession | None = await self._session.scalar(stmt)
        return result

    async def add_movement(self, movement: CashMovement) -> CashMovement:
        self._session.add(movement)
        await self._session.flush()
        return movement

    async def list_movements(self, session_id: int) -> list[CashMovement]:
        stmt = select(CashMovement).where(CashMovement.session_id == session_id)
        result = await self._session.scalars(stmt)
        return list(result)

    async def cash_out_in_range(self, store_id: int, start: datetime, end: datetime) -> Decimal:
        """本店在 [start, end)（依 movement.created_at）的現金出帳合計 = BUYOUT_OUT + 寄售付款。

        供趨勢報表以任意時間桶彙整現金支出（與 session 無關，依事件時間落桶）。
        """
        stmt = select(func.coalesce(func.sum(CashMovement.amount), 0)).where(
            CashMovement.store_id == store_id,
            CashMovement.created_at >= start,
            CashMovement.created_at < end,
            CashMovement.type.in_(
                (CashMovementType.BUYOUT_OUT, CashMovementType.CONSIGNMENT_PAYOUT_OUT)
            ),
        )
        return Decimal((await self._session.scalar(stmt)) or 0)

    async def list_sessions_in_range(
        self, store_id: int, start: datetime, end: datetime
    ) -> list[CashSession]:
        """opened_at 落在 [start, end) 的本店 session（唯讀報表用，依 id 排序）。"""
        stmt = (
            select(CashSession)
            .where(
                CashSession.store_id == store_id,
                CashSession.opened_at >= start,
                CashSession.opened_at < end,
            )
            .order_by(CashSession.id)
        )
        result = await self._session.scalars(stmt)
        return list(result)
