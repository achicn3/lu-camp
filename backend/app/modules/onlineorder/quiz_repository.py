"""引導推薦設定的存取（店別範圍；每店一份）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.onlineorder.models import OnlineMenuQuiz


class MenuQuizRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, store_id: int, *, for_update: bool = False) -> OnlineMenuQuiz | None:
        stmt = select(OnlineMenuQuiz).where(OnlineMenuQuiz.store_id == store_id)
        if for_update:
            stmt = stmt.with_for_update()
        row: OnlineMenuQuiz | None = await self._session.scalar(stmt)
        return row

    async def save(self, row: OnlineMenuQuiz) -> None:
        self._session.add(row)
        await self._session.flush()
