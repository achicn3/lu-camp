"""組合包袋裝條碼的資料存取（ADR-028）。一律以 store_id 範圍過濾。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.campaigns.models import BundlePack, BundlePackItem


class BundlePackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, pack: BundlePack, items: list[BundlePackItem]) -> BundlePack:
        self._session.add(pack)
        await self._session.flush()
        for item in items:
            item.pack_id = pack.id
        self._session.add_all(items)
        await self._session.flush()
        return pack

    async def get(self, store_id: int, pack_id: int) -> BundlePack | None:
        pack: BundlePack | None = await self._session.scalar(
            select(BundlePack).where(BundlePack.id == pack_id, BundlePack.store_id == store_id)
        )
        return pack

    async def get_for_update(self, store_id: int, pack_id: int) -> BundlePack | None:
        pack: BundlePack | None = await self._session.scalar(
            select(BundlePack)
            .where(BundlePack.id == pack_id, BundlePack.store_id == store_id)
            .with_for_update()
        )
        return pack

    async def get_active_by_code(self, store_id: int, code: str) -> BundlePack | None:
        pack: BundlePack | None = await self._session.scalar(
            select(BundlePack).where(
                BundlePack.code == code,
                BundlePack.store_id == store_id,
                BundlePack.is_active.is_(True),
            )
        )
        return pack

    async def code_exists(self, code: str) -> bool:
        """條碼全域唯一（標籤不分店印），不只本店。"""
        found = await self._session.scalar(select(BundlePack.id).where(BundlePack.code == code))
        return found is not None

    async def list_for_campaign(self, store_id: int, campaign_id: int) -> list[BundlePack]:
        return list(
            (
                await self._session.scalars(
                    select(BundlePack)
                    .where(BundlePack.store_id == store_id, BundlePack.campaign_id == campaign_id)
                    .order_by(BundlePack.id)
                )
            ).all()
        )

    async def items_for(self, store_id: int, pack_ids: list[int]) -> list[BundlePackItem]:
        if not pack_ids:
            return []
        return list(
            (
                await self._session.scalars(
                    select(BundlePackItem)
                    .where(
                        BundlePackItem.store_id == store_id, BundlePackItem.pack_id.in_(pack_ids)
                    )
                    .order_by(BundlePackItem.id)
                )
            ).all()
        )
