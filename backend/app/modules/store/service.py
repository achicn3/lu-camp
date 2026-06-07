"""store 業務邏輯：收據抬頭取得。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.store.models import Store
from app.modules.store.repository import StoreRepository
from app.shared.exceptions import StoreNotFound


class StoreService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = StoreRepository(session)

    async def get_receipt_header(self, store_id: int) -> Store:
        """取得收據抬頭來源門市；不存在則丟 `StoreNotFound`。"""
        store = await self._repo.get_by_id(store_id)
        if store is None:
            raise StoreNotFound(f"找不到門市 {store_id}")
        return store
