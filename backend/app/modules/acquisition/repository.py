"""acquisition 資料存取層（唯一直接碰本模組 ORM 的層）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.acquisition.models import Acquisition


class AcquisitionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, acquisition: Acquisition) -> Acquisition:
        self._session.add(acquisition)
        await self._session.flush()
        return acquisition

    async def get(self, store_id: int, acquisition_id: int) -> Acquisition | None:
        stmt = select(Acquisition).where(
            Acquisition.id == acquisition_id, Acquisition.store_id == store_id
        )
        result: Acquisition | None = await self._session.scalar(stmt)
        return result
