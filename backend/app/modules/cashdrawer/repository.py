"""cashdrawer 資料存取層（唯一直接碰 ORM 的層）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.models import CashMovement, CashSession
from app.shared.enums import CashSessionStatus


class CashDrawerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_session(self, cash_session: CashSession) -> CashSession:
        self._session.add(cash_session)
        await self._session.flush()
        return cash_session

    async def get_open_session(self, store_id: int) -> CashSession | None:
        stmt = select(CashSession).where(
            CashSession.store_id == store_id,
            CashSession.status == CashSessionStatus.OPEN,
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
