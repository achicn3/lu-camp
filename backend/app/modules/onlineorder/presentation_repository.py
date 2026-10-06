"""Store-scoped online menu presentation persistence."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.onlineorder.models import OnlineMenuPresentation


class MenuPresentationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, store_id: int, item_id: int) -> OnlineMenuPresentation | None:
        return await self._session.scalar(
            select(OnlineMenuPresentation).where(
                OnlineMenuPresentation.store_id == store_id,
                OnlineMenuPresentation.menu_item_id == item_id,
            )
        )

    async def for_items(
        self, store_id: int, item_ids: Sequence[int]
    ) -> list[OnlineMenuPresentation]:
        if not item_ids:
            return []
        return list(
            await self._session.scalars(
                select(OnlineMenuPresentation).where(
                    OnlineMenuPresentation.store_id == store_id,
                    OnlineMenuPresentation.menu_item_id.in_(item_ids),
                )
            )
        )

    async def save(self, row: OnlineMenuPresentation) -> None:
        self._session.add(row)
        await self._session.flush()
