"""線上帶回家商品設定的存取（店別範圍）。"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.onlineorder.models import OnlineRetailListing


class RetailListingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_store(self, store_id: int) -> Sequence[OnlineRetailListing]:
        return list(
            await self._session.scalars(
                select(OnlineRetailListing)
                .where(OnlineRetailListing.store_id == store_id)
                .order_by(OnlineRetailListing.sort_order, OnlineRetailListing.id)
            )
        )

    async def get(
        self, store_id: int, listing_id: int, *, for_update: bool = False
    ) -> OnlineRetailListing | None:
        stmt = select(OnlineRetailListing).where(
            OnlineRetailListing.store_id == store_id, OnlineRetailListing.id == listing_id
        )
        if for_update:
            stmt = stmt.with_for_update()
        row: OnlineRetailListing | None = await self._session.scalar(stmt)
        return row

    async def by_product(self, store_id: int, product_id: int) -> OnlineRetailListing | None:
        row: OnlineRetailListing | None = await self._session.scalar(
            select(OnlineRetailListing).where(
                OnlineRetailListing.store_id == store_id,
                OnlineRetailListing.catalog_product_id == product_id,
            )
        )
        return row

    async def save(self, row: OnlineRetailListing) -> None:
        self._session.add(row)
        await self._session.flush()

    async def delete(self, row: OnlineRetailListing) -> None:
        await self._session.delete(row)
        await self._session.flush()
