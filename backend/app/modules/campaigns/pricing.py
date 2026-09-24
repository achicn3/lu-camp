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
6. **買 N 送 M**（P3，整車才算得出來，見 `price_cart`）：適用的件依單價由高到低排，
   每 N+M 件一組，組內最便宜的 M 件免費；免費額按組內各件價格比例分攤到整組（裁示 3、4），
   最大餘數法、加總不差一元。寄售品不參加（裁示 7）。每件只參加一個買 N 送 M（依活動 id）。
   可疊加的疊在第 2 步後的價格上（已用不可疊加活動的件不參加）；不可疊加的以原價算、
   不跟任何活動併用，只有比第 2 步划算才採用（裁示 1、9）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from app.core.money import discounted_price
from app.shared.enums import CampaignItemKind, CampaignKind, CampaignTargetType
from app.shared.exceptions import SaleLineInvalid

_MIN_UNIT_PRICE = Decimal(1)
# 單件就能算價的活動類型（第 2 步）；買 N 送 M 要看整車（第 3 步）。
_UNIT_KINDS = frozenset(
    {CampaignKind.PERCENT_OFF, CampaignKind.FIXED_PRICE, CampaignKind.AMOUNT_OFF}
)
# 買 N 送 M 一律排除寄售品（裁示 7）。
_CONSIGNMENT_KINDS = frozenset({CampaignItemKind.CONSIGNMENT_SERIALIZED})
# 一筆裡參加買 N 送 M 的件數上限：要逐件分組，不設限會被超大數量拖垮（Codex 審查）。
MAX_PROMO_UNITS = 10_000


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
    buy_qty: int | None = None
    free_qty: int | None = None


@dataclass(frozen=True)
class CampaignPrice:
    """一件商品的活動定價結果。沒有折扣時 original_unit_price 為 None、allocations 為空。"""

    unit_price: Decimal
    original_unit_price: Decimal | None
    discount_per_unit: Decimal
    allocations: tuple[tuple[int, Decimal], ...] = field(default=())
    """每件的折讓由哪些活動貢獻：(campaign_id, 每件折讓)，依套用順序、加總＝discount_per_unit。"""


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
    applicable = sorted(
        (c for c in campaigns if c.kind in _UNIT_KINDS and campaign_applies(c, item)),
        key=lambda c: c.id,
    )
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


@dataclass(frozen=True)
class CartLine:
    """整車定價的一行。item 為 None＝這行不參加任何活動（贈品、餐飲、寄售散裝）。"""

    item: PromoItem | None
    unit_price: Decimal
    """原含稅單價。"""
    qty: int


@dataclass(frozen=True)
class LinePrice:
    """一行的活動定價結果（本行合計；買 N 送 M 分攤後各件可能不同價）。"""

    list_unit_price: Decimal
    qty: int
    line_total: Decimal
    allocations: tuple[tuple[int, Decimal], ...] = ()
    """本行的活動折讓由哪些活動貢獻：(campaign_id, 本行合計)，依套用順序。"""
    free_units: int = 0
    """本行有幾件是買 N 送 M 裡「送的那件」（顯示用；金額已分攤到整組）。"""

    @property
    def list_total(self) -> Decimal:
        return self.list_unit_price * self.qty

    @property
    def discount_amount(self) -> Decimal:
        return self.list_total - self.line_total

    @property
    def original_unit_price(self) -> Decimal | None:
        """有折扣才有（沿用 sale_lines.original_unit_price 的語意）。"""
        return self.list_unit_price if self.allocations else None

    @property
    def unit_price(self) -> Decimal:
        """成交單價：各件同價時就是那個價；分攤後不整除時為平均、四捨五入到整數元。"""
        return (self.line_total / self.qty).quantize(Decimal(1), rounding=ROUND_HALF_UP)

    @property
    def primary_campaign_id(self) -> int | None:
        """貢獻最多的活動（同額取先套用的）；舊欄位 sale_lines.campaign_id 用。"""
        if not self.allocations:
            return None
        return max(self.allocations, key=lambda a: a[1])[0]


@dataclass
class _Unit:
    """展開成單件的狀態（買 N 送 M 以件為單位分組）。"""

    line: int
    item: PromoItem
    list_price: Decimal
    price: Decimal
    allocations: list[tuple[int, Decimal]]
    non_stackable: bool
    """第 2 步用的是不可疊加活動。"""
    claimed: bool = False
    free: bool = False


def _allocate(total: Decimal, weights: Sequence[Decimal], caps: Sequence[Decimal]) -> list[Decimal]:
    """把整數元 total 按 weights 比例分下去（最大餘數法），每份不超過 cap；分不完的捨去。"""
    weight_sum = sum(weights, Decimal(0))
    exact = [total * w / weight_sum for w in weights]
    shares = [
        min(cap, e.to_integral_value(rounding=ROUND_FLOOR))
        for e, cap in zip(exact, caps, strict=True)
    ]
    left = total - sum(shares, Decimal(0))
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - int(exact[i])), i))
    while left > 0:
        progressed = False
        for i in order:
            if left > 0 and shares[i] < caps[i]:
                shares[i] += 1
                left -= 1
                progressed = True
        if not progressed:
            break
    return shares


def _group_prices(
    units: Sequence[_Unit], campaign: PromoCampaign, *, use_list_price: bool
) -> list[Decimal] | None:
    """一組 N+M 件（已由高到低排）的免費額分攤；回傳每件折讓，湊不出折讓回 None。"""
    assert campaign.free_qty is not None
    base = [u.list_price if use_list_price else u.price for u in units]
    free_amount = sum(base[-campaign.free_qty :], Decimal(0))
    if free_amount <= 0:  # 送的都是 0 元品（收購允許 0 元）：沒東西可分，也不能除以零
        return None
    caps = [max(Decimal(0), b - _MIN_UNIT_PRICE) for b in base]
    shares = _allocate(free_amount, base, caps)
    return shares if sum(shares, Decimal(0)) > 0 else None


def _apply_buy_n_get_m(units: list[_Unit], campaign: PromoCampaign) -> None:
    """套一個買 N 送 M 活動到尚未參加其他買 N 送 M 的件上（見模組說明第 6 點）。"""
    assert campaign.buy_qty is not None and campaign.free_qty is not None
    size = campaign.buy_qty + campaign.free_qty
    eligible = [
        u
        for u in units
        if not u.claimed
        and _bngm_may_apply(campaign, u.item)
        and (not campaign.stackable or not u.non_stackable)
    ]
    use_list = not campaign.stackable
    eligible.sort(
        key=lambda u: -(u.list_price if use_list else u.price)
    )  # 穩定排序：同價依購物車序
    grouped = eligible[: len(eligible) // size * size]
    if not grouped:
        return
    # (件, 折讓, 是否為送的那件)：每組最後 M 件（最便宜的）是送的。
    plans: list[tuple[_Unit, Decimal, bool]] = []
    for start in range(0, len(grouped), size):
        group = grouped[start : start + size]
        shares = _group_prices(group, campaign, use_list_price=use_list)
        if shares is None:
            continue
        plans.extend(
            (u, share, position >= campaign.buy_qty)
            for position, (u, share) in enumerate(zip(group, shares, strict=True))
        )
    if not plans:
        return
    if use_list:
        # 不可疊加：以原價算，跟第 2 步比，對客人比較划算才採用（同價維持第 2 步）。
        new_total = sum((u.list_price - share for u, share, _ in plans), Decimal(0))
        if new_total >= sum((u.price for u, _, _ in plans), Decimal(0)):
            return
    for u, share, free in plans:
        if use_list:
            u.price, u.allocations = u.list_price, []
        u.price -= share
        if share > 0:  # 分到 0 元不是折讓（sale_line_campaigns 也要求 > 0），但仍算在這組裡
            u.allocations.append((campaign.id, share))
        u.claimed = True
        u.free = free


def price_cart(lines: Sequence[CartLine], campaigns: Sequence[PromoCampaign]) -> list[LinePrice]:
    """整台購物車的活動定價（報價、結帳共用；docs/40 §5）：先逐件（第 2 步），再買 N 送 M。

    只有可能參加買 N 送 M 的行才展開成單件（上限 `MAX_PROMO_UNITS` 件）；其他行照單價 × 數量。
    """
    by_id = {c.id: c for c in campaigns}
    bngms = sorted((c for c in campaigns if c.kind == CampaignKind.BUY_N_GET_M), key=lambda c: c.id)
    units: list[_Unit] = []
    results: list[LinePrice | None] = []
    for index, cart_line in enumerate(lines):
        item = cart_line.item
        if item is None or cart_line.qty <= 0:
            results.append(
                LinePrice(cart_line.unit_price, cart_line.qty, cart_line.unit_price * cart_line.qty)
            )
            continue
        single = price_unit(cart_line.unit_price, item, campaigns)
        if not any(_bngm_may_apply(c, item) for c in bngms):
            results.append(
                LinePrice(
                    cart_line.unit_price,
                    cart_line.qty,
                    single.unit_price * cart_line.qty,
                    tuple((cid, amount * cart_line.qty) for cid, amount in single.allocations),
                )
            )
            continue
        if len(units) + cart_line.qty > MAX_PROMO_UNITS:
            raise SaleLineInvalid(f"參加買幾送幾的商品一次最多 {MAX_PROMO_UNITS} 件，請分筆結帳")
        non_stackable = any(not by_id[cid].stackable for cid, _ in single.allocations)
        units.extend(
            _Unit(
                index,
                item,
                cart_line.unit_price,
                single.unit_price,
                list(single.allocations),
                non_stackable,
            )
            for _ in range(cart_line.qty)
        )
        results.append(None)  # 買 N 送 M 算完再由各件彙總
    for campaign in bngms:
        _apply_buy_n_get_m(units, campaign)
    by_line: dict[int, list[_Unit]] = {}
    for u in units:
        by_line.setdefault(u.line, []).append(u)
    return [
        result if result is not None else _line_price(lines[index], by_line[index])
        for index, result in enumerate(results)
    ]


def _bngm_may_apply(campaign: PromoCampaign, item: PromoItem) -> bool:
    return item.kind not in _CONSIGNMENT_KINDS and campaign_applies(campaign, item)


def _line_price(cart_line: CartLine, mine: Sequence[_Unit]) -> LinePrice:
    """把一行展開的各件彙總回本行合計（各活動的折讓依第一次出現的順序）。"""
    totals: dict[int, Decimal] = {}
    for u in mine:
        for cid, amount in u.allocations:
            totals[cid] = totals.get(cid, Decimal(0)) + amount
    return LinePrice(
        cart_line.unit_price,
        cart_line.qty,
        sum((u.price for u in mine), Decimal(0)),
        tuple(totals.items()),
        sum(1 for u in mine if u.free),
    )
