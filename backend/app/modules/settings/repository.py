"""settings 資料存取層（唯一直接碰 ORM 的層）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.settings.models import PremiumRateHistory, StoreSettings


class SettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_store(self, store_id: int) -> StoreSettings | None:
        stmt = select(StoreSettings).where(StoreSettings.store_id == store_id)
        result: StoreSettings | None = await self._session.scalar(stmt)
        return result

    async def add(self, settings: StoreSettings) -> StoreSettings:
        self._session.add(settings)
        await self._session.flush()
        return settings

    async def add_history(self, row: PremiumRateHistory) -> PremiumRateHistory:
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_history(
        self, store_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[PremiumRateHistory]:
        stmt = (
            select(PremiumRateHistory)
            .where(PremiumRateHistory.store_id == store_id)
            .order_by(PremiumRateHistory.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.scalars(stmt)).all())
