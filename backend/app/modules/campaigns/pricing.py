"""門市活動 v2 定價（純函式：無 DB、無 I/O，可完整單元測試；docs/40 §5）。

一件商品的活動折後單價怎麼算——報價、結帳、客顯共用這一支，口徑才不會漂。

## 規則（店主裁示 2026-09-23）

1. **適用**：品項種類要在活動的種類開關內（沿用 docs/21 的 applies_*）；有「包含」條件時
   至少符合一個；符合任何「排除」條件就不適用。餐飲不進來（呼叫端不會傳）。
2. **候選**：每個「不可疊加」活動**單用**各算一個價；「可疊加」的活動**全部依序套用**算一個價
   （打折乘比例、折金額直接減、特價取較低者）。
   不可疊加＝不跟任何活動併用，所以它不會再疊別的，別的也不會疊上它。
3. **挑最划算**：取單價最低的候選；同價時依序偏好 id 較小的不可疊加活動、最後才是疊加組合
   ——同一台購物車每次算出來都一樣，退貨與報表才對得起來。
4. **捨入**：每套一個活動四捨五入一次（HALF_UP 整數元），疊加依活動 id 由小到大。
   這樣每個活動分到的折讓都是整數元，加總剛好等於總折讓（不必事後再分攤）。
5. **下限**：單價不低於 1 元——0 元是贈品，要走贈品流程（docs/32 紅線）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.money import discounted_price
from app.shared.enums import CampaignItemKind, CampaignKind, CampaignTargetType

_MIN_UNIT_PRICE = Decimal(1)


@dataclass(frozen=True)
class PromoItem:
    """要定價的一件商品：種類與可被活動範圍比對的各種 id（沒有就 None）。"""

    kind: CampaignItemKind
    category_id: int | None = None
    brand_id: int | None = None
    product_model_id: int | None = None
    serialized_item_id: int | None = None
    catalog_product_id: int | None = None
    bulk_basket_id: int | None = None


@dataclass(frozen=True)
class PromoCampaign:
    """一個生效中的活動：打折（discount_pct）、指定特價（fixed_price）或每件折金額（amount_off）。"""

    id: int
    name: str
    discount_pct: int | None
    stackable: bool
    item_kinds: frozenset[CampaignItemKind]
    includes: tuple[tuple[CampaignTargetType, int], ...] = ()
    excludes: tuple[tuple[CampaignTargetType, int], ...] = ()
    kind: CampaignKind = CampaignKind.PERCENT_OFF
    fixed_price: Decimal | None = None
    amount_off: Decimal | None = None


@dataclass(frozen=True)
class CampaignPrice:
    """一件商品的活動定價結果。沒有折扣時 original_unit_price 為 None、allocations 為空。"""

    unit_price: Decimal
    original_unit_price: Decimal | None
    discount_per_unit: Decimal
    allocations: tuple[tuple[int, Decimal], ...] = field(default=())
    """每件的折讓由哪些活動貢獻：(campaign_id, 每件折讓)，依套用順序、加總＝discount_per_unit。"""

    @property
    def primary_campaign_id(self) -> int | None:
        """貢獻最多的活動（同額取先套用的）；舊欄位 sale_lines.campaign_id 用。"""
        if not self.allocations:
            return None
        return max(self.allocations, key=lambda a: a[1])[0]


def _target_matches(target: tuple[CampaignTargetType, int], item: PromoItem) -> bool:
    target_type, target_id = target
    value = {
        CampaignTargetType.CATEGORY: item.category_id,
        CampaignTargetType.BRAND: item.brand_id,
        CampaignTargetType.PRODUCT_MODEL: item.product_model_id,
        CampaignTargetType.SERIALIZED_ITEM: item.serialized_item_id,
        CampaignTargetType.CATALOG_PRODUCT: item.catalog_product_id,
        CampaignTargetType.BULK_BASKET: item.bulk_basket_id,
    }[target_type]
    return value is not None and value == target_id


def campaign_applies(campaign: PromoCampaign, item: PromoItem) -> bool:
    """活動是否適用這件商品（種類開關＋包含／排除條件）。"""
    if item.kind not in campaign.item_kinds:
        return False
    if campaign.includes and not any(_target_matches(t, item) for t in campaign.includes):
        return False
    return not any(_target_matches(t, item) for t in campaign.excludes)


def _apply(price: Decimal, campaign: PromoCampaign) -> Decimal:
    """套一個活動後的單價（不低於 1 元）。特價取「特價」與「目前價」較低者（docs/40 §5）。"""
    if campaign.kind == CampaignKind.FIXED_PRICE:
        assert campaign.fixed_price is not None
        new_price = min(price, campaign.fixed_price)
    elif campaign.kind == CampaignKind.AMOUNT_OFF:
        assert campaign.amount_off is not None
        new_price = price - campaign.amount_off
    else:
        assert campaign.discount_pct is not None
        new_price = Decimal(discounted_price(price, campaign.discount_pct))
    return max(_MIN_UNIT_PRICE, new_price)


def _chain(unit_price: Decimal, campaigns: Sequence[PromoCampaign]) -> CampaignPrice:
    """依序套用（每步捨入一次），記下每個活動實際折了多少。"""
    price = unit_price
    allocations: list[tuple[int, Decimal]] = []
    for c in campaigns:
        new_price = _apply(price, c) if price > _MIN_UNIT_PRICE else price
        if new_price < price:
            allocations.append((c.id, price - new_price))
        price = new_price
    return CampaignPrice(price, unit_price, unit_price - price, tuple(allocations))


def price_unit(
    unit_price: Decimal, item: PromoItem, campaigns: Sequence[PromoCampaign]
) -> CampaignPrice:
    """一件商品的活動折後單價：各不可疊加單用 vs 可疊加連乘，取最低（見模組說明）。"""
    applicable = sorted((c for c in campaigns if campaign_applies(c, item)), key=lambda c: c.id)
    candidates = [_chain(unit_price, [c]) for c in applicable if not c.stackable]
    stackables = [c for c in applicable if c.stackable]
    if stackables:
        candidates.append(_chain(unit_price, stackables))

    best: CampaignPrice | None = None
    for candidate in candidates:  # 同價保留先出現的：候選順序即偏好順序
        if best is None or candidate.unit_price < best.unit_price:
            best = candidate
    if best is None or best.unit_price >= unit_price:
        return CampaignPrice(unit_price, None, Decimal(0))
    return best
