"""campaigns 資料存取層（唯一直接碰 ORM 的層）。"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.campaigns.models import Campaign
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

    async def list(self, store_id: int, *, status: CampaignStatus | None = None) -> list[Campaign]:
        stmt = select(Campaign).where(Campaign.store_id == store_id)
        if status is not None:
            stmt = stmt.where(Campaign.status == status)
        stmt = stmt.order_by(Campaign.id.desc())
        return list((await self._session.scalars(stmt)).all())

    async def get_effective(self, store_id: int, now: datetime) -> Campaign | None:
        """目前生效中活動：status=ACTIVE 且 now ∈ [starts_at, ends_at)（同店至多一個）。"""
        stmt = select(Campaign).where(
            Campaign.store_id == store_id,
            Campaign.status == CampaignStatus.ACTIVE,
            Campaign.starts_at <= now,
            Campaign.ends_at > now,
        )
        result: Campaign | None = await self._session.scalar(stmt)
        return result
