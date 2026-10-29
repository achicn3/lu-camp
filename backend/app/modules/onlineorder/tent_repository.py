"""寄售帳篷官網資料的存取（店別範圍）。"""

from collections.abc import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.onlineorder.models import (
    OnlineTentListing,
    OnlineTentListingPhoto,
    OnlineTentPublication,
)


class TentListingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_item(
        self, store_id: int, item_id: int, *, for_update: bool = False
    ) -> OnlineTentListing | None:
        stmt = select(OnlineTentListing).where(
            OnlineTentListing.store_id == store_id,
            OnlineTentListing.serialized_item_id == item_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        row: OnlineTentListing | None = await self._session.scalar(stmt)
        return row

    async def by_items(self, store_id: int, item_ids: list[int]) -> dict[int, OnlineTentListing]:
        if not item_ids:
            return {}
        rows = await self._session.scalars(
            select(OnlineTentListing).where(
                OnlineTentListing.store_id == store_id,
                OnlineTentListing.serialized_item_id.in_(item_ids),
            )
        )
        return {r.serialized_item_id: r for r in rows}

    async def published(self, store_id: int) -> Sequence[OnlineTentListing]:
        return list(
            await self._session.scalars(
                select(OnlineTentListing)
                .where(OnlineTentListing.store_id == store_id, OnlineTentListing.published)
                .order_by(OnlineTentListing.id)
            )
        )

    async def save(self, row: OnlineTentListing) -> None:
        self._session.add(row)
        await self._session.flush()

    async def photos(self, listing_ids: list[int]) -> dict[int, list[str]]:
        """每筆的照片雜湊（依位置排）。"""
        out: dict[int, list[str]] = {i: [] for i in listing_ids}
        if not listing_ids:
            return out
        rows = await self._session.scalars(
            select(OnlineTentListingPhoto)
            .where(OnlineTentListingPhoto.listing_id.in_(listing_ids))
            .order_by(OnlineTentListingPhoto.listing_id, OnlineTentListingPhoto.position)
        )
        for r in rows:
            out[r.listing_id].append(r.photo_sha256)
        return out

    async def replace_photos(self, row: OnlineTentListing, shas: list[str]) -> None:
        """整組換掉（先刪再寫，位置才不會撞唯一鍵）。"""
        await self._session.execute(
            delete(OnlineTentListingPhoto).where(OnlineTentListingPhoto.listing_id == row.id)
        )
        await self._session.flush()
        self._session.add_all(
            OnlineTentListingPhoto(
                store_id=row.store_id, listing_id=row.id, position=i, photo_sha256=sha
            )
            for i, sha in enumerate(shas)
        )
        await self._session.flush()

    async def clear_reserved(self, store_id: int, listing_ids: list[int]) -> int:
        if not listing_ids:
            return 0
        result = await self._session.execute(
            update(OnlineTentListing)
            .where(OnlineTentListing.store_id == store_id, OnlineTentListing.id.in_(listing_ids))
            .values(reserved=False)
        )
        await self._session.flush()
        return int(getattr(result, "rowcount", 0) or 0)

    async def publication(
        self, store_id: int, cloud: str, *, for_update: bool = False
    ) -> OnlineTentPublication | None:
        stmt = select(OnlineTentPublication).where(
            OnlineTentPublication.store_id == store_id, OnlineTentPublication.cloud == cloud
        )
        if for_update:
            stmt = stmt.with_for_update()
        row: OnlineTentPublication | None = await self._session.scalar(stmt)
        return row

    async def save_publication(self, row: OnlineTentPublication) -> None:
        self._session.add(row)
        await self._session.flush()
