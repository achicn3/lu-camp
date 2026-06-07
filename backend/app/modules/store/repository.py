"""store 資料存取層（唯一直接碰 ORM 的層）。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.store.models import Store


class StoreRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, store_id: int) -> Store | None:
        return await self._session.get(Store, store_id)
