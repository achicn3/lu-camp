"""線上點餐（店內端）的資料存取。"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.onlineorder.models import OnlineMenuPublication, OnlinePushedMedia, OnlineTableCode


class OnlineOrderRepository:
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

    async def max_version(self, store_id: int) -> int:
        value: int | None = await self._session.scalar(
            select(func.max(OnlineMenuPublication.version)).where(
                OnlineMenuPublication.store_id == store_id
            )
        )
        return value or 0

    async def add_publication(self, row: OnlineMenuPublication) -> None:
        self._session.add(row)
        await self._session.flush()

    async def pushed(self, store_id: int, kind: str, hashes: Sequence[str]) -> set[str]:
        if not hashes:
            return set()
        rows = await self._session.scalars(
            select(OnlinePushedMedia.sha256).where(
                OnlinePushedMedia.store_id == store_id,
                OnlinePushedMedia.kind == kind,
                OnlinePushedMedia.sha256.in_(hashes),
            )
        )
        return set(rows)

    async def mark_pushed(self, store_id: int, kind: str, sha256: str) -> None:
        await self._session.execute(
            insert(OnlinePushedMedia)
            .values(store_id=store_id, kind=kind, sha256=sha256)
            .on_conflict_do_nothing(constraint="uq_online_pushed_media")
        )

    async def active_tables(
        self, store_id: int, *, for_update: bool = False
    ) -> list[OnlineTableCode]:
        stmt = (
            select(OnlineTableCode)
            .where(OnlineTableCode.store_id == store_id, OnlineTableCode.retired_at.is_(None))
            .order_by(OnlineTableCode.id)
        )
        if for_update:
            stmt = stmt.with_for_update()
        return list(await self._session.scalars(stmt))

    async def add_table(self, row: OnlineTableCode) -> None:
        self._session.add(row)
        await self._session.flush()

    async def retire(self, row: OnlineTableCode, at: datetime) -> None:
        row.retired_at = at
        await self._session.flush()
