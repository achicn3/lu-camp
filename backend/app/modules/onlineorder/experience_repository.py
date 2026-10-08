"""手沖體驗卡的存取（店別範圍）。"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.onlineorder.models import OnlineMenuExperience


class MenuExperienceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_store(self, store_id: int) -> Sequence[OnlineMenuExperience]:
        return list(
            await self._session.scalars(
                select(OnlineMenuExperience)
                .where(OnlineMenuExperience.store_id == store_id)
                .order_by(OnlineMenuExperience.sort_order, OnlineMenuExperience.id)
            )
        )

    async def get(
        self, store_id: int, experience_id: int, *, for_update: bool = False
    ) -> OnlineMenuExperience | None:
        stmt = select(OnlineMenuExperience).where(
            OnlineMenuExperience.store_id == store_id,
            OnlineMenuExperience.id == experience_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        row: OnlineMenuExperience | None = await self._session.scalar(stmt)
        return row

    async def save(self, row: OnlineMenuExperience) -> None:
        self._session.add(row)
        await self._session.flush()

    async def delete(self, row: OnlineMenuExperience) -> None:
        await self._session.delete(row)
        await self._session.flush()
