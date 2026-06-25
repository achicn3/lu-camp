"""campaigns 業務邏輯：門市活動 CRUD 與狀態機（docs/21）。

建立為 DRAFT；啟用→ACTIVE（同店至多一個，DB partial unique 為最終保證）；結束→ENDED；
作廢→CANCELLED。所有變更（建立/啟用/結束/作廢）皆寫 audit_log（§5「改價」級敏感操作）。
本層只 flush、不 commit（由呼叫端控制）。折扣計算屬 C2 結帳整合，不在此。
"""

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import DISCOUNT_PCT_MAX, DISCOUNT_PCT_MIN
from app.modules.campaigns.models import Campaign
from app.modules.campaigns.repository import CampaignRepository
from app.shared.enums import CampaignStatus
from app.shared.exceptions import (
    CampaignConflict,
    CampaignNotFound,
    InvalidDiscountPct,
)


class CampaignService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = CampaignRepository(session)

    async def create_campaign(
        self,
        store_id: int,
        *,
        name: str,
        discount_pct: int,
        starts_at: datetime,
        ends_at: datetime,
        applies_owned_serialized: bool,
        applies_owned_bulk: bool,
        applies_catalog: bool,
        applies_consignment: bool,
        created_by: int,
    ) -> Campaign:
        """建立活動（DRAFT）。驗證折扣 1-99、區間 ends>starts、名稱非空；寫稽核。"""
        if not name.strip():
            raise CampaignConflict("活動名稱不可為空")
        if not DISCOUNT_PCT_MIN <= discount_pct <= DISCOUNT_PCT_MAX:
            raise InvalidDiscountPct(
                f"discount_pct 須介於 {DISCOUNT_PCT_MIN}-{DISCOUNT_PCT_MAX}，收到 {discount_pct}"
            )
        if ends_at <= starts_at:
            raise CampaignConflict("活動結束時間必須晚於開始時間")
        campaign = Campaign(
            store_id=store_id,
            name=name.strip(),
            discount_pct=discount_pct,
            starts_at=starts_at,
            ends_at=ends_at,
            applies_owned_serialized=applies_owned_serialized,
            applies_owned_bulk=applies_owned_bulk,
            applies_catalog=applies_catalog,
            applies_consignment=applies_consignment,
            status=CampaignStatus.DRAFT,
            created_by=created_by,
        )
        saved = await self._repo.add(campaign)
        await self._audit(store_id, created_by, "CAMPAIGN_CREATE", saved, before=None)
        return saved

    async def activate(self, store_id: int, campaign_id: int, *, actor_user_id: int) -> Campaign:
        """DRAFT → ACTIVE。同店至多一個 ACTIVE（DB partial unique 擋併發/重複啟用）。"""
        campaign = await self._lock(store_id, campaign_id)
        if campaign.status != CampaignStatus.DRAFT:
            raise CampaignConflict(f"只有草稿（DRAFT）活動可啟用，目前狀態 {campaign.status.value}")
        before = campaign.status.value
        campaign.status = CampaignStatus.ACTIVE
        try:
            await self._session.flush()
        except IntegrityError as exc:  # 已有生效中活動（partial unique）
            raise CampaignConflict("本店已有生效中的活動，請先結束舊活動") from exc
        await self._session.refresh(campaign)  # onupdate updated_at 由 DB 設，刷回避免 lazy IO
        await self._audit(store_id, actor_user_id, "CAMPAIGN_ACTIVATE", campaign, before=before)
        return campaign

    async def end(self, store_id: int, campaign_id: int, *, actor_user_id: int) -> Campaign:
        """ACTIVE → ENDED（手動結束生效中活動）。"""
        campaign = await self._lock(store_id, campaign_id)
        if campaign.status != CampaignStatus.ACTIVE:
            raise CampaignConflict(
                f"只有生效中（ACTIVE）活動可結束，目前狀態 {campaign.status.value}"
            )
        before = campaign.status.value
        campaign.status = CampaignStatus.ENDED
        await self._session.flush()
        await self._session.refresh(campaign)
        await self._audit(store_id, actor_user_id, "CAMPAIGN_END", campaign, before=before)
        return campaign

    async def cancel(self, store_id: int, campaign_id: int, *, actor_user_id: int) -> Campaign:
        """DRAFT / ACTIVE → CANCELLED（作廢）。"""
        campaign = await self._lock(store_id, campaign_id)
        if campaign.status not in (CampaignStatus.DRAFT, CampaignStatus.ACTIVE):
            raise CampaignConflict(f"只有草稿或生效中活動可作廢，目前狀態 {campaign.status.value}")
        before = campaign.status.value
        campaign.status = CampaignStatus.CANCELLED
        await self._session.flush()
        await self._session.refresh(campaign)
        await self._audit(store_id, actor_user_id, "CAMPAIGN_CANCEL", campaign, before=before)
        return campaign

    async def get(self, store_id: int, campaign_id: int) -> Campaign | None:
        return await self._repo.get(store_id, campaign_id)

    async def list_campaigns(
        self,
        store_id: int,
        *,
        status: CampaignStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Campaign]:
        return await self._repo.list(store_id, status=status, limit=limit, offset=offset)

    async def get_effective(self, store_id: int, now: datetime) -> Campaign | None:
        """目前生效中活動（C2 結帳套折扣用）；無則 None。"""
        return await self._repo.get_effective(store_id, now)

    async def _lock(self, store_id: int, campaign_id: int) -> Campaign:
        campaign = await self._repo.get_for_update(store_id, campaign_id)
        if campaign is None:
            raise CampaignNotFound(f"找不到活動 {campaign_id}")
        return campaign

    async def _audit(
        self,
        store_id: int,
        actor_user_id: int,
        action: str,
        campaign: Campaign,
        *,
        before: str | None,
    ) -> None:
        after: dict[str, object] = {
            "status": campaign.status.value,
            "name": campaign.name,
            "discount_pct": campaign.discount_pct,
        }
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type="campaign",
            entity_id=str(campaign.id),
            before=None if before is None else {"status": before},
            after=after,
        )
