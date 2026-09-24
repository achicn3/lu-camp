"""campaigns 資料存取層（唯一直接碰 ORM 的層）。"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.campaigns.models import (
    Campaign,
    CampaignBundleSlot,
    CampaignBundleSlotTarget,
    CampaignTarget,
)
from app.shared.enums import CampaignStatus


class CampaignRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, campaign: Campaign) -> Campaign:
        self._session.add(campaign)
        await self._session.flush()
        return campaign

    async def get(self, store_id: int, campaign_id: int) -> Campaign | None:
        stmt = select(Campaign).where(Campaign.id == campaign_id, Campaign.store_id == store_id)
        result: Campaign | None = await self._session.scalar(stmt)
        return result

    async def get_for_update(self, store_id: int, campaign_id: int) -> Campaign | None:
        """取本店活動列並上 row lock（狀態轉移前序列化，避免併發雙重轉移）。"""
        stmt = (
            select(Campaign)
            .where(Campaign.id == campaign_id, Campaign.store_id == store_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result: Campaign | None = await self._session.scalar(stmt)
        return result

    async def list_campaigns(
        self,
        store_id: int,
        *,
        status: CampaignStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Campaign]:
        stmt = select(Campaign).where(Campaign.store_id == store_id)
        if status is not None:
            stmt = stmt.where(Campaign.status == status)
        stmt = stmt.order_by(Campaign.id.desc())
        if limit is not None:
            stmt = stmt.limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    async def count(self, store_id: int, *, status: CampaignStatus | None = None) -> int:
        """符合同一組篩選的活動總筆數（不分頁；清單頁算總頁數用）。"""
        stmt = select(func.count()).select_from(Campaign).where(Campaign.store_id == store_id)
        if status is not None:
            stmt = stmt.where(Campaign.status == status)
        return int((await self._session.scalar(stmt)) or 0)

    async def list_effective(self, store_id: int, now: datetime) -> list[Campaign]:
        """目前生效中的活動（可多個）：status=ACTIVE 且 now ∈ [starts_at, ends_at)；依 id。"""
        stmt = (
            select(Campaign)
            .where(
                Campaign.store_id == store_id,
                Campaign.status == CampaignStatus.ACTIVE,
                Campaign.starts_at <= now,
                Campaign.ends_at > now,
            )
            .order_by(Campaign.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def add_targets(self, targets: list[CampaignTarget]) -> None:
        self._session.add_all(targets)
        await self._session.flush()

    async def add_bundle_slot(
        self, slot: CampaignBundleSlot, targets: list[CampaignBundleSlotTarget]
    ) -> None:
        self._session.add(slot)
        await self._session.flush()
        for target in targets:
            target.slot_id = slot.id
        self._session.add_all(targets)
        await self._session.flush()

    async def bundle_slots_for(
        self, store_id: int, campaign_ids: list[int]
    ) -> list[tuple[CampaignBundleSlot, list[CampaignBundleSlotTarget]]]:
        """一批活動的組合格子與各格範圍（依活動、格號排序）。"""
        if not campaign_ids:
            return []
        slots = list(
            (
                await self._session.scalars(
                    select(CampaignBundleSlot)
                    .where(
                        CampaignBundleSlot.store_id == store_id,
                        CampaignBundleSlot.campaign_id.in_(campaign_ids),
                    )
                    .order_by(CampaignBundleSlot.campaign_id, CampaignBundleSlot.slot_no)
                )
            ).all()
        )
        if not slots:
            return []
        targets = (
            await self._session.scalars(
                select(CampaignBundleSlotTarget)
                .where(
                    CampaignBundleSlotTarget.store_id == store_id,
                    CampaignBundleSlotTarget.slot_id.in_([s.id for s in slots]),
                )
                .order_by(CampaignBundleSlotTarget.id)
            )
        ).all()
        by_slot: dict[int, list[CampaignBundleSlotTarget]] = {}
        for t in targets:
            by_slot.setdefault(t.slot_id, []).append(t)
        return [(s, by_slot.get(s.id, [])) for s in slots]

    async def targets_for(self, store_id: int, campaign_ids: list[int]) -> list[CampaignTarget]:
        """一批活動的範圍條件（依 id，順序穩定）。"""
        if not campaign_ids:
            return []
        stmt = (
            select(CampaignTarget)
            .where(
                CampaignTarget.store_id == store_id,
                CampaignTarget.campaign_id.in_(campaign_ids),
            )
            .order_by(CampaignTarget.id)
        )
        return list((await self._session.scalars(stmt)).all())
