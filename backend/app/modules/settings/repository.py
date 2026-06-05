"""settings 資料存取層（唯一直接碰 ORM 的層）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.settings.models import StoreSettings


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
