"""人氣標籤設定的存取（店別範圍；每店一列）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.onlineorder.models import OnlineMenuPopularity


class PopularityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, store_id: int, *, for_update: bool = False) -> OnlineMenuPopularity | None:
        stmt = select(OnlineMenuPopularity).where(OnlineMenuPopularity.store_id == store_id)
        if for_update:
            stmt = stmt.with_for_update()
        row: OnlineMenuPopularity | None = await self._session.scalar(stmt)
        return row

    async def save(self, row: OnlineMenuPopularity) -> None:
        self._session.add(row)
        await self._session.flush()
