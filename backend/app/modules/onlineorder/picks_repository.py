"""店員推薦清單的存取（店別範圍；每店一份）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.onlineorder.models import OnlineStaffPicks


class StaffPicksRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, store_id: int, *, for_update: bool = False) -> OnlineStaffPicks | None:
        stmt = select(OnlineStaffPicks).where(OnlineStaffPicks.store_id == store_id)
        if for_update:
            stmt = stmt.with_for_update()
        row: OnlineStaffPicks | None = await self._session.scalar(stmt)
        return row

    async def save(self, row: OnlineStaffPicks) -> None:
        self._session.add(row)
        await self._session.flush()
