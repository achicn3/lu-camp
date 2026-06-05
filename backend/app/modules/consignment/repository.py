"""consignment 資料存取層（唯一直接碰 ORM 的層）。

T11 只需在售出時新增 PENDING 結算；查詢與付款（→PAID）等屬 Phase 4 再加。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.consignment.models import ConsignmentSettlement


class ConsignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, settlement: ConsignmentSettlement) -> ConsignmentSettlement:
        self._session.add(settlement)
        await self._session.flush()
        return settlement
