"""Persistence for the per-store current menu availability delivery state."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.onlineorder.models import OnlineMenuAvailability, OnlineMenuPublication


class AvailabilityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest_publication(self, store_id: int) -> OnlineMenuPublication | None:
        row: OnlineMenuPublication | None = await self._session.scalar(
            select(OnlineMenuPublication)
            .where(OnlineMenuPublication.store_id == store_id)
            .order_by(OnlineMenuPublication.version.desc())
            .limit(1)
        )
        return row

    async def locked_state(self, store_id: int) -> OnlineMenuAvailability:
        # Insert-or-ignore before locking avoids two first captures creating separate revisions.
        await self._session.execute(
            insert(OnlineMenuAvailability)
            .values(
                store_id=store_id,
                menu_version=0,
                revision=0,
                payload={},
                delivery_state="DELIVERED",
            )
            .on_conflict_do_nothing(index_elements=[OnlineMenuAvailability.store_id])
        )
        row = await self._session.scalar(
            select(OnlineMenuAvailability)
            .where(OnlineMenuAvailability.store_id == store_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert row is not None
        return row
