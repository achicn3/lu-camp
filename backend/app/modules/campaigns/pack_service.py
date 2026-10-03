"""組合包袋裝條碼（ADR-028；店主 2026-10-04）：一張條碼代表一袋，掃了把袋裡的商品加進購物車。

價錢與分攤、整組退、報表全部沿用所屬的組合價活動（docs/40 P4），這裡只管：
- 建立時驗證袋裡的內容「單獨結帳剛好湊成這個活動的一組」——用結帳同一支計價引擎試算，不另寫比對；
- 掃碼時回袋裡每件商品的現況（售價、備註、庫存夠不夠），以及活動現在有沒有生效。
本層只 flush、不 commit（由呼叫端控制）。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.campaigns.models import BundlePack, BundlePackItem, Campaign
from app.modules.campaigns.pack_repository import BundlePackRepository
from app.modules.campaigns.pricing import CartLine, PromoItem, price_cart
from app.modules.campaigns.promo_items import (
    basket_promo_item,
    catalog_promo_item,
    serialized_promo_item,
)
from app.modules.campaigns.schemas import BundlePackItemInput
from app.modules.campaigns.service import CampaignService
from app.modules.inventory.basket_service import BasketView, BulkBasketService
from app.modules.inventory.service import InventoryService
from app.shared.enums import (
    BulkLotStatus,
    BundlePackItemType,
    CampaignKind,
    CampaignStatus,
    OwnershipType,
    SerializedItemStatus,
)
from app.shared.exceptions import (
    BundlePackInvalid,
    BundlePackNotFound,
    CampaignConflict,
    CampaignNotFound,
    InvalidCampaignTarget,
)

_CODE_RANDOM_LEN = 10
_CODE_ATTEMPTS = 5
_CLOSED = frozenset({CampaignStatus.ENDED, CampaignStatus.CANCELLED})


def new_pack_code(store_id: int) -> str:
    """袋裝條碼：P＋店號＋10 碼十六進位（與序號品 S、散裝 L、販售籃 K 區分）。"""
    return f"P{store_id}-{uuid4().hex[:_CODE_RANDOM_LEN].upper()}"


@dataclass(frozen=True)
class _Resolved:
    """袋裡一項商品的現況。"""

    code: str
    name: str
    label: str
    unit_price: Decimal
    note: str | None
    brand_id: int | None
    stock: int
    """目前可賣幾件。"""
    promo: PromoItem
    problem: str | None
    """不能放進袋子／不能照袋子賣的原因（寄售、不在庫、下架、停用）；可以就是 None。"""


@dataclass(frozen=True)
class PackItemView:
    item_type: BundlePackItemType
    target_id: int
    qty: int
    label: str


@dataclass(frozen=True)
class PackView:
    pack: BundlePack
    items: list[PackItemView]


@dataclass(frozen=True)
class PackScanItem:
    item_type: BundlePackItemType
    target_id: int
    qty: int
    code: str
    name: str
    unit_price: Decimal
    note: str | None
    brand_id: int | None
    stock: int
    available: bool
    unavailable_reason: str | None


@dataclass(frozen=True)
class PackScan:
    pack: BundlePack
    campaign_name: str
    bundle_price: Decimal | None
    campaign_effective: bool
    items: list[PackScanItem]


def _basket_note(view: BasketView) -> str | None:
    """與 POS 掃販售籃時的提醒同一套組法：籃子備註＋還有貨的各批收購備註（去重）。"""
    notes = [view.basket.note] + [
        s.note for s in view.sources if s.status == BulkLotStatus.ON_SALE and s.remaining_qty > 0
    ]
    unique = list(dict.fromkeys(n.strip() for n in notes if n and n.strip()))
    return "；".join(unique) if unique else None


def _target_of(item: BundlePackItem) -> int:
    target = item.serialized_item_id or item.catalog_product_id or item.bulk_basket_id
    assert target is not None  # DB CHECK 保證恰好一個
    return target


def _effective(campaign: Campaign, now: datetime) -> bool:
    return campaign.status is CampaignStatus.ACTIVE and campaign.starts_at <= now < campaign.ends_at


class BundlePackService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = BundlePackRepository(session)
        self._campaigns = CampaignService(session)
        self._inventory = InventoryService(session)
        self._baskets = BulkBasketService(session)

    async def create_pack(
        self,
        store_id: int,
        campaign_id: int,
        *,
        name: str,
        items: Sequence[BundlePackItemInput],
        actor_user_id: int,
    ) -> BundlePack:
        """建一袋：活動須是本店未結束的組合價；內容單獨結帳要剛好湊成一組（ADR-028）。"""
        campaign = await self._campaigns.get(store_id, campaign_id)
        if campaign is None:
            raise CampaignNotFound(f"找不到活動 {campaign_id}")
        if campaign.kind is not CampaignKind.BUNDLE:
            raise CampaignConflict("只有組合價活動可以建袋裝條碼")
        if campaign.status in _CLOSED:
            raise CampaignConflict("活動已結束或作廢，不能再建袋裝條碼")
        keys = [(i.item_type, i.target_id) for i in items]
        if len(set(keys)) != len(keys):
            raise BundlePackInvalid("同一樣商品重複列了，請合併成一項、填件數")
        resolved = [await self._resolve_required(store_id, i) for i in items]
        for item, r in zip(items, resolved, strict=True):
            if r.problem is not None:
                raise BundlePackInvalid(f"「{r.label}」{r.problem}")
            if r.stock < item.qty:
                raise BundlePackInvalid(f"「{r.label}」庫存只剩 {r.stock}，不夠放 {item.qty} 件")
        await self._assert_one_group(store_id, campaign, items, resolved)

        pack = await self._repo.add(
            BundlePack(
                store_id=store_id,
                campaign_id=campaign.id,
                code=await self._unique_code(store_id),
                name=name.strip(),
                created_by=actor_user_id,
            ),
            [
                BundlePackItem(
                    store_id=store_id,
                    item_type=i.item_type,
                    serialized_item_id=i.target_id
                    if i.item_type is BundlePackItemType.SERIALIZED
                    else None,
                    catalog_product_id=i.target_id
                    if i.item_type is BundlePackItemType.CATALOG
                    else None,
                    bulk_basket_id=i.target_id
                    if i.item_type is BundlePackItemType.BULK_BASKET
                    else None,
                    qty=i.qty,
                )
                for i in items
            ],
        )
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="bundle_pack.create",
            entity_type="bundle_pack",
            entity_id=str(pack.id),
            after={
                "campaign_id": campaign.id,
                "code": pack.code,
                "name": pack.name,
                "items": "、".join(
                    f"{r.label}×{i.qty}" for i, r in zip(items, resolved, strict=True)
                ),
            },
        )
        return pack

    async def get(self, store_id: int, pack_id: int) -> BundlePack:
        pack = await self._repo.get(store_id, pack_id)
        if pack is None:
            raise BundlePackNotFound(f"找不到袋裝條碼 {pack_id}")
        return pack

    async def list_packs(self, store_id: int, campaign_id: int) -> list[PackView]:
        """某個組合價活動的全部袋子（含停用的，依建立先後）。"""
        if await self._campaigns.get(store_id, campaign_id) is None:
            raise CampaignNotFound(f"找不到活動 {campaign_id}")
        packs = await self._repo.list_for_campaign(store_id, campaign_id)
        return [
            PackView(pack, [await self._item_view(store_id, item) for item in items])
            for pack, items in await self._with_items(store_id, packs)
        ]

    async def deactivate(self, store_id: int, pack_id: int, *, actor_user_id: int) -> BundlePack:
        """停用：之後掃不到（例如袋子拆了、標籤作廢）。已停用就原樣回傳。"""
        pack = await self._repo.get_for_update(store_id, pack_id)
        if pack is None:
            raise BundlePackNotFound(f"找不到袋裝條碼 {pack_id}")
        if pack.is_active:
            pack.is_active = False
            await self._session.flush()
            await write_audit_log(
                self._session,
                store_id=store_id,
                actor_user_id=actor_user_id,
                action="bundle_pack.deactivate",
                entity_type="bundle_pack",
                entity_id=str(pack.id),
                before={"is_active": True},
                after={"is_active": False, "code": pack.code},
            )
        return pack

    async def scan(self, store_id: int, code: str) -> PackScan:
        """POS 掃袋裝條碼：袋裡每件商品的現況，與組合價現在有沒有生效。停用／他店 → 找不到。"""
        pack = await self._repo.get_active_by_code(store_id, code)
        if pack is None:
            raise BundlePackNotFound(f"找不到此袋裝條碼：{code}")
        campaign = await self._campaigns.get(store_id, pack.campaign_id)
        assert campaign is not None  # FK 保證
        items = await self._repo.items_for(store_id, [pack.id])
        scanned: list[PackScanItem] = []
        for item in items:
            r = await self._resolve(store_id, item.item_type, _target_of(item))
            if r is None:
                reason: str | None = "商品已不存在"
                scanned.append(
                    PackScanItem(
                        item.item_type,
                        _target_of(item),
                        item.qty,
                        "",
                        "（已不存在的商品）",
                        Decimal(0),
                        None,
                        None,
                        0,
                        False,
                        reason,
                    )
                )
                continue
            reason = r.problem or (f"庫存只剩 {r.stock}" if r.stock < item.qty else None)
            scanned.append(
                PackScanItem(
                    item_type=item.item_type,
                    target_id=_target_of(item),
                    qty=item.qty,
                    code=r.code,
                    name=r.name,
                    unit_price=r.unit_price,
                    note=r.note,
                    brand_id=r.brand_id,
                    stock=r.stock,
                    available=reason is None,
                    unavailable_reason=reason,
                )
            )
        return PackScan(
            pack=pack,
            campaign_name=campaign.name,
            bundle_price=campaign.bundle_price,
            campaign_effective=_effective(campaign, datetime.now(UTC)),
            items=scanned,
        )

    async def _assert_one_group(
        self,
        store_id: int,
        campaign: Campaign,
        items: Sequence[BundlePackItemInput],
        resolved: Sequence[_Resolved],
    ) -> None:
        """袋裡內容只放這個活動試算：必須剛好一組、每件都在組內（與結帳同一支引擎）。"""
        promos = await self._campaigns.promos_for(store_id, [campaign])
        priced = price_cart(
            [CartLine(r.promo, r.unit_price, i.qty) for i, r in zip(items, resolved, strict=True)],
            promos,
        )
        groups = {g for line in priced for g, _, _ in line.bundle_groups}
        if not groups:
            raise BundlePackInvalid(
                "袋裡的商品湊不成這個組合價（少放了、放錯，或組合價沒有比原價便宜）"
            )
        if len(groups) > 1:
            raise BundlePackInvalid(f"袋裡的商品夠湊 {len(groups)} 組，一袋請只放一組")
        loose = [
            r.label
            for line, r in zip(priced, resolved, strict=True)
            if sum(n for _, _, n in line.bundle_groups) < line.qty
        ]
        if loose:
            raise BundlePackInvalid(f"多放了不在組合裡的件數：{'、'.join(loose)}")

    async def _unique_code(self, store_id: int) -> str:
        for _ in range(_CODE_ATTEMPTS):
            code = new_pack_code(store_id)
            if not await self._repo.code_exists(code):
                return code
        raise RuntimeError("袋裝條碼連續撞號，請重試")

    async def _with_items(
        self, store_id: int, packs: list[BundlePack]
    ) -> list[tuple[BundlePack, list[BundlePackItem]]]:
        items = await self._repo.items_for(store_id, [p.id for p in packs])
        by_pack: dict[int, list[BundlePackItem]] = {}
        for item in items:
            by_pack.setdefault(item.pack_id, []).append(item)
        return [(p, by_pack.get(p.id, [])) for p in packs]

    async def _item_view(self, store_id: int, item: BundlePackItem) -> PackItemView:
        r = await self._resolve(store_id, item.item_type, _target_of(item))
        return PackItemView(
            item.item_type,
            _target_of(item),
            item.qty,
            "（已不存在的商品）" if r is None else r.label,
        )

    async def _resolve_required(self, store_id: int, item: BundlePackItemInput) -> _Resolved:
        r = await self._resolve(store_id, item.item_type, item.target_id)
        if r is None:
            raise InvalidCampaignTarget(f"找不到商品（{item.item_type.value} {item.target_id}）")
        return r

    async def _resolve(
        self, store_id: int, item_type: BundlePackItemType, target_id: int
    ) -> _Resolved | None:
        """袋裡一項的現況；不存在或不屬本店 → None。"""
        if item_type is BundlePackItemType.SERIALIZED:
            item = await self._inventory.get_serialized_by_id(store_id, target_id)
            if item is None:
                return None
            in_stock = item.status is SerializedItemStatus.IN_STOCK
            problem = (
                "是寄售品，寄售品不能進組合包"
                if item.ownership_type is OwnershipType.CONSIGNMENT
                else None
                if in_stock
                else "不在庫（可能已單獨賣出）"
            )
            return _Resolved(
                code=item.item_code,
                name=item.name,
                label=f"{item.name}（{item.item_code}）",
                unit_price=item.listed_price,
                note=item.note,
                brand_id=item.brand_id,
                stock=1 if in_stock else 0,
                promo=serialized_promo_item(item),
                problem=problem,
            )
        if item_type is BundlePackItemType.CATALOG:
            product = await self._inventory.get_catalog(store_id, target_id)
            if product is None:
                return None
            return _Resolved(
                code=product.sku,
                name=product.name,
                label=product.name,
                unit_price=product.unit_price,
                note=product.note,
                brand_id=product.brand_id,
                stock=product.quantity_on_hand if product.is_active else 0,
                promo=catalog_promo_item(product),
                problem=None if product.is_active else "已下架",
            )
        view = await self._baskets.get(store_id, target_id)
        if view is None:
            return None
        basket = view.basket
        return _Resolved(
            code=basket.code,
            name=basket.name,
            label=basket.name,
            unit_price=basket.unit_price,
            note=_basket_note(view),
            brand_id=basket.brand_id,
            stock=view.remaining_qty if basket.is_active else 0,
            promo=basket_promo_item(basket),
            problem=None if basket.is_active else "販售籃已停用",
        )
