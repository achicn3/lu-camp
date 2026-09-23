"""campaigns 業務邏輯：門市活動 CRUD、狀態機與生效中活動的定價輸入（docs/21、docs/40）。

建立為 DRAFT；啟用→ACTIVE（v2 起可同時多個）；結束→ENDED；作廢→CANCELLED。
所有變更（建立/啟用/結束/作廢）皆寫 audit_log（§5「改價」級敏感操作）。
本層只 flush、不 commit（由呼叫端控制）。每件商品怎麼折見 campaigns/pricing.py（純函式）。
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import DISCOUNT_PCT_MAX, DISCOUNT_PCT_MIN
from app.modules.campaigns.models import Campaign, CampaignTarget
from app.modules.campaigns.pricing import PromoCampaign
from app.modules.campaigns.repository import CampaignRepository
from app.modules.campaigns.schemas import CampaignRead, CampaignTargetInput, CampaignTargetRead
from app.modules.inventory.basket_service import BulkBasketService
from app.modules.inventory.service import InventoryService
from app.shared.enums import (
    CampaignItemKind,
    CampaignStatus,
    CampaignTargetMode,
    CampaignTargetType,
)
from app.shared.exceptions import (
    CampaignConflict,
    CampaignNotFound,
    InvalidCampaignTarget,
    InvalidDiscountPct,
)


def _item_kinds(campaign: Campaign) -> frozenset[CampaignItemKind]:
    """docs/21 的四個種類開關 → 定價用的種類集合。"""
    flags = {
        CampaignItemKind.OWNED_SERIALIZED: campaign.applies_owned_serialized,
        CampaignItemKind.CONSIGNMENT_SERIALIZED: campaign.applies_consignment,
        CampaignItemKind.OWNED_BULK: campaign.applies_owned_bulk,
        CampaignItemKind.CATALOG: campaign.applies_catalog,
    }
    return frozenset(kind for kind, on in flags.items() if on)


_READ_COLUMNS = (
    "id",
    "store_id",
    "name",
    "discount_pct",
    "applies_owned_serialized",
    "applies_owned_bulk",
    "applies_catalog",
    "applies_consignment",
    "starts_at",
    "ends_at",
    "status",
    "stackable",
    "created_by",
    "created_at",
    "updated_at",
)


class CampaignService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = CampaignRepository(session)
        self._inventory = InventoryService(session)
        self._baskets = BulkBasketService(session)

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
        stackable: bool = False,
        targets: list[CampaignTargetInput] | None = None,
    ) -> Campaign:
        """建立活動（DRAFT）。驗證折扣 1-99、區間 ends>starts、名稱非空、範圍屬本店；寫稽核。

        範圍條件重複的只存一筆；任何一條指向不存在或他店的項目 → InvalidCampaignTarget（整筆不建）。
        """
        if not name.strip():
            raise CampaignConflict("活動名稱不可為空")
        if not DISCOUNT_PCT_MIN <= discount_pct <= DISCOUNT_PCT_MAX:
            raise InvalidDiscountPct(
                f"折扣百分比須介於 {DISCOUNT_PCT_MIN}-{DISCOUNT_PCT_MAX}，收到 {discount_pct}"
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
            stackable=stackable,
            status=CampaignStatus.DRAFT,
            created_by=created_by,
        )
        unique_targets = list(
            dict.fromkeys((t.mode, t.target_type, t.target_id) for t in targets or [])
        )
        for _mode, target_type, target_id in unique_targets:
            if await self._target_label(store_id, target_type, target_id) is None:
                raise InvalidCampaignTarget(
                    f"活動範圍裡有找不到或不屬本店的項目（{target_type.value} #{target_id}）"
                )
        saved = await self._repo.add(campaign)
        await self._repo.add_targets(
            [
                CampaignTarget(
                    store_id=store_id,
                    campaign_id=saved.id,
                    mode=mode,
                    target_type=target_type,
                    target_id=target_id,
                )
                for mode, target_type, target_id in unique_targets
            ]
        )
        await self._audit(store_id, created_by, "CAMPAIGN_CREATE", saved, before=None)
        return saved

    async def activate(self, store_id: int, campaign_id: int, *, actor_user_id: int) -> Campaign:
        """DRAFT → ACTIVE。v2 起同店可同時多個生效中（docs/40）。"""
        campaign = await self._lock(store_id, campaign_id)
        if campaign.status != CampaignStatus.DRAFT:
            raise CampaignConflict(
                "只有『草稿』狀態的活動可以啟用。這筆已經不是草稿了，請重新整理頁面看最新狀態"
            )
        before = campaign.status.value
        campaign.status = CampaignStatus.ACTIVE
        await self._session.flush()
        await self._session.refresh(campaign)  # onupdate updated_at 由 DB 設，刷回避免 lazy IO
        await self._audit(store_id, actor_user_id, "CAMPAIGN_ACTIVATE", campaign, before=before)
        return campaign

    async def end(self, store_id: int, campaign_id: int, *, actor_user_id: int) -> Campaign:
        """ACTIVE → ENDED（手動結束生效中活動）。"""
        campaign = await self._lock(store_id, campaign_id)
        if campaign.status != CampaignStatus.ACTIVE:
            raise CampaignConflict(
                "只有『進行中』的活動可以結束。這筆已經不是進行中了，請重新整理頁面看最新狀態"
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
        return await self._repo.list_campaigns(store_id, status=status, limit=limit, offset=offset)

    async def count_campaigns(self, store_id: int, *, status: CampaignStatus | None = None) -> int:
        """符合同一組篩選的活動總筆數（清單頁算總頁數用）。"""
        return await self._repo.count(store_id, status=status)

    async def effective_promos(self, store_id: int, now: datetime) -> list[PromoCampaign]:
        """目前生效中的全部活動，整理成定價輸入（含範圍條件）；結帳／報價／客顯共用。"""
        campaigns = await self._repo.list_effective(store_id, now)
        targets = await self._repo.targets_for(store_id, [c.id for c in campaigns])
        by_campaign: dict[int, list[CampaignTarget]] = {}
        for t in targets:
            by_campaign.setdefault(t.campaign_id, []).append(t)
        return [
            PromoCampaign(
                id=c.id,
                name=c.name,
                discount_pct=c.discount_pct,
                stackable=c.stackable,
                item_kinds=_item_kinds(c),
                includes=tuple(
                    (t.target_type, t.target_id)
                    for t in by_campaign.get(c.id, [])
                    if t.mode == CampaignTargetMode.INCLUDE
                ),
                excludes=tuple(
                    (t.target_type, t.target_id)
                    for t in by_campaign.get(c.id, [])
                    if t.mode == CampaignTargetMode.EXCLUDE
                ),
            )
            for c in campaigns
        ]

    async def to_reads(self, store_id: int, campaigns: list[Campaign]) -> list[CampaignRead]:
        """API 輸出：活動＋範圍條件（附名稱）。範圍一次查完，名稱逐條解析（條件數有上限）。"""
        targets = await self._repo.targets_for(store_id, [c.id for c in campaigns])
        reads: dict[int, list[CampaignTargetRead]] = {}
        for t in targets:
            label = await self._target_label(store_id, t.target_type, t.target_id)
            reads.setdefault(t.campaign_id, []).append(
                CampaignTargetRead(
                    mode=t.mode,
                    target_type=t.target_type,
                    target_id=t.target_id,
                    # 建立後項目被刪（例如沒賣過的商品真刪）時仍要列得出來，名稱退回 id。
                    label=label if label is not None else f"#{t.target_id}（已不存在）",
                )
            )
        return [
            CampaignRead.model_validate(
                {
                    **{col: getattr(c, col) for col in _READ_COLUMNS},
                    "targets": reads.get(c.id, []),
                }
            )
            for c in campaigns
        ]

    async def to_read(self, store_id: int, campaign: Campaign) -> CampaignRead:
        return (await self.to_reads(store_id, [campaign]))[0]

    async def _target_label(
        self, store_id: int, target_type: CampaignTargetType, target_id: int
    ) -> str | None:
        """範圍條件的顯示名稱；不存在或不屬本店 → None（建立時據此拒絕）。"""
        if target_type == CampaignTargetType.CATEGORY:
            category = await self._inventory.get_category(store_id, target_id)
            return None if category is None else category.name
        if target_type == CampaignTargetType.BRAND:
            brand = await self._inventory.get_brand(store_id, target_id)
            return None if brand is None else brand.name
        if target_type == CampaignTargetType.PRODUCT_MODEL:
            model = await self._inventory.get_product_model(store_id, target_id)
            if model is None:
                return None
            brand = await self._inventory.get_brand(store_id, model.brand_id)
            return model.name if brand is None else f"{brand.name} {model.name}"
        if target_type == CampaignTargetType.SERIALIZED_ITEM:
            item = await self._inventory.get_serialized_by_id(store_id, target_id)
            return None if item is None else f"{item.name}（{item.item_code}）"
        if target_type == CampaignTargetType.CATALOG_PRODUCT:
            product = await self._inventory.get_catalog(store_id, target_id)
            return None if product is None else product.name
        view = await self._baskets.get(store_id, target_id)
        return None if view is None else view.basket.name

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
