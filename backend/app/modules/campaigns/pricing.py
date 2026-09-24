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
6. **組合價**（P4，整車第一步，見 `_apply_bundles`）：每個格子（範圍＋件數）都湊齊才成一組，
   每格挑最貴的符合件；只有比這些件走其他活動更划算才成組（裁示 1），成組的件不再參加其他活動。
   組合價按組內各件原價比例分攤（最大餘數法、每件至少 1 元、加總＝組合價）。寄售品不進組合包。
   組合價勾「可疊加」時，分攤完再套適用的可疊加單件活動；不可疊加的一律不併用。
7. **買 N 送 M**（P3，整車才算得出來，見 `price_cart`）：適用的件依單價由高到低排，
   每 N+M 件一組，組內最便宜的 M 件免費；免費額按組內各件價格比例分攤到整組（裁示 3、4），
   最大餘數法、加總不差一元。寄售品不參加（裁示 7）。每件只參加一個買 N 送 M（依活動 id）。
   可疊加的疊在第 2 步後的價格上（已用不可疊加活動的件不參加）；不可疊加的以原價算、
   不跟任何活動併用，只有比第 2 步划算才採用（裁示 1、9）。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
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
    bundle_price: Decimal | None = None
    bundle_slots: tuple[BundleSlot, ...] = ()


@dataclass(frozen=True)
class BundleSlot:
    """組合包的一個格子：符合任一範圍條件的商品，要湊 qty 件。"""

    qty: int
    includes: tuple[tuple[CampaignTargetType, int], ...]


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
    free_requested: bool = False
    """店員指定「送這件」（裁示 4）：成組時優先當送的那件，取代組內預設送的最便宜那件。"""


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
    free_campaign_id: int | None = None
    """送的那件屬於哪個買 N 送 M 活動（沒有送的件為 None）。"""
    buy_n_get_m_units: int = 0
    """本行有幾件成組參加了買 N 送 M（含送的件）。"""
    buy_n_get_m_eligible: bool = False
    """本行可能參加買 N 送 M（有適用的活動）——沒湊進組的也可以被指定「送這件」。"""
    bundle_groups: tuple[tuple[int, int, int], ...] = ()
    """本行有哪些件進了組合包：(組號（本車內從 0 起）, 活動 id, 件數)。"""

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


@dataclass(eq=False)
class _Unit:
    """展開成單件的狀態（買 N 送 M 以件為單位分組）。

    比對一律用身分（eq=False）：同一行的各件內容相同，用值比對會換錯件。
    """

    line: int
    item: PromoItem
    list_price: Decimal
    price: Decimal
    allocations: list[tuple[int, Decimal]]
    non_stackable: bool
    """第 2 步用的是不可疊加活動。"""
    free_requested: bool = False
    claimed: bool = False
    free_campaign: int | None = None
    bundle: tuple[int, int] | None = None
    """(組號, 活動 id)：這件進了哪一組組合包。"""


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

    def price_of(u: _Unit) -> Decimal:
        return u.list_price if use_list else u.price

    eligible.sort(key=lambda u: -price_of(u))  # 穩定排序：同價依購物車序
    count = len(eligible) // size * size
    if count == 0:
        return
    # 每組前 N 件付錢、後 M 件（最便宜的）送；再依店員的指定調換送的那件。
    groups = [eligible[start : start + size] for start in range(0, count, size)]
    _apply_free_requests(groups, eligible[count:], campaign.buy_qty, price_of)
    # (件, 折讓, 是否為送的那件)
    plans: list[tuple[_Unit, Decimal, bool]] = []
    for group in groups:
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
        u.free_campaign = campaign.id if free else None


def _apply_free_requests(
    groups: list[list[_Unit]],
    leftover: list[_Unit],
    buy_qty: int,
    price_of: Callable[[_Unit], Decimal],
) -> None:
    """店員指定送的件放進送的位置（每組後 M 格）。

    送的位置有限：指定的件依排序（貴的先）取前「組數×M」件，其餘指定不生效。
    每件優先換掉自己那組預設送的件；自己那組的送位都被指定件佔了（或本來沒成組），
    就換掉其他組（從最後一組找）還沒被指定佔用的送位。被換下的件回到它原本的位置。
    """
    slots = sum(len(g) - buy_qty for g in groups)
    ordered = [u for g in groups for u in g] + leftover
    ordered.sort(key=lambda u: -price_of(u))  # 與分組同一把尺；穩定排序：同價依原本順序
    chosen = [u for u in ordered if u.free_requested][:slots]
    chosen_ids = {id(u) for u in chosen}
    # 位置表與各組還沒被指定佔用的送位：每次調換都是常數時間（逐件重掃在上限時會卡住 worker）。
    where: dict[int, tuple[list[_Unit], int, int | None]] = {}
    for gi, group in enumerate(groups):
        for i, u in enumerate(group):
            where[id(u)] = (group, i, gi)
    for i, u in enumerate(leftover):
        where[id(u)] = (leftover, i, None)
    open_slots = [
        [i for i in range(len(g) - 1, buy_qty - 1, -1) if id(g[i]) not in chosen_ids]
        for g in groups
    ]
    fallback = deque(reversed(range(len(groups))))  # 其他組從最後一組找起
    for unit in chosen:
        origin, index, home = where[id(unit)]
        if home is not None and index >= buy_qty:
            continue  # 已經在送的位置
        if home is not None and open_slots[home]:
            target = home
        else:
            while fallback and not open_slots[fallback[0]]:
                fallback.popleft()
            if not fallback:
                break
            target = fallback[0]
        slot = open_slots[target].pop()
        group = groups[target]
        displaced = group[slot]
        origin[index] = displaced
        where[id(displaced)] = (origin, index, home)
        group[slot] = unit
        where[id(unit)] = (group, slot, target)


def price_cart(lines: Sequence[CartLine], campaigns: Sequence[PromoCampaign]) -> list[LinePrice]:
    """整台購物車的活動定價（報價、結帳共用；docs/40 §5）：先逐件（第 2 步），再買 N 送 M。

    只有可能參加買 N 送 M 的行才展開成單件（上限 `MAX_PROMO_UNITS` 件）；其他行照單價 × 數量。
    """
    by_id = {c.id: c for c in campaigns}
    bngms = sorted((c for c in campaigns if c.kind == CampaignKind.BUY_N_GET_M), key=lambda c: c.id)
    bundles = sorted((c for c in campaigns if c.kind == CampaignKind.BUNDLE), key=lambda c: c.id)
    cart_wide = bngms + bundles  # 要看整車才算得出來的活動
    units: list[_Unit] = []
    results: list[LinePrice | None] = []
    bngm_lines: set[int] = set()  # 有買 N 送 M 適用的行（POS 據此提供「改送這件」）
    for index, cart_line in enumerate(lines):
        item = cart_line.item
        if item is None or cart_line.qty <= 0:
            results.append(
                LinePrice(cart_line.unit_price, cart_line.qty, cart_line.unit_price * cart_line.qty)
            )
            continue
        single = price_unit(cart_line.unit_price, item, campaigns)
        if not any(_bngm_may_apply(c, item) for c in cart_wide):
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
            raise SaleLineInvalid(
                f"參加買幾送幾／組合價的商品一次最多 {MAX_PROMO_UNITS} 件，請分筆結帳"
            )
        non_stackable = any(not by_id[cid].stackable for cid, _ in single.allocations)
        units.extend(
            _Unit(
                index,
                item,
                cart_line.unit_price,
                single.unit_price,
                list(single.allocations),
                non_stackable,
                cart_line.free_requested,
            )
            for _ in range(cart_line.qty)
        )
        if any(_bngm_may_apply(c, item) for c in bngms):
            bngm_lines.add(index)
        results.append(None)  # 組合價、買 N 送 M 算完再由各件彙總
    _apply_bundles(units, bundles, [c for c in campaigns if c.kind in _UNIT_KINDS and c.stackable])
    for campaign in bngms:
        _apply_buy_n_get_m(units, campaign)
    by_line: dict[int, list[_Unit]] = {}
    for u in units:
        by_line.setdefault(u.line, []).append(u)
    return [
        result
        if result is not None
        else _line_price(lines[index], by_line[index], bngm=index in bngm_lines)
        for index, result in enumerate(results)
    ]


def _bngm_may_apply(campaign: PromoCampaign, item: PromoItem) -> bool:
    return item.kind not in _CONSIGNMENT_KINDS and campaign_applies(campaign, item)


def _line_price(cart_line: CartLine, mine: Sequence[_Unit], *, bngm: bool) -> LinePrice:
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
        sum(1 for u in mine if u.free_campaign is not None),
        next((u.free_campaign for u in mine if u.free_campaign is not None), None),
        sum(1 for u in mine if u.claimed and u.bundle is None),
        # 全部件都進了組合包的行，就沒有能「改送」的件了。
        buy_n_get_m_eligible=bngm and any(u.bundle is None for u in mine),
        bundle_groups=_bundle_groups(mine),
    )


def _bundle_groups(mine: Sequence[_Unit]) -> tuple[tuple[int, int, int], ...]:
    counts: dict[tuple[int, int], int] = {}
    for u in mine:
        if u.bundle is not None:
            counts[u.bundle] = counts.get(u.bundle, 0) + 1
    return tuple((group, cid, qty) for (group, cid), qty in sorted(counts.items()))


def _slot_matches(campaign: PromoCampaign, slot: BundleSlot, item: PromoItem) -> bool:
    return _bngm_may_apply(campaign, item) and any(_target_matches(t, item) for t in slot.includes)


def _apply_bundles(
    units: list[_Unit], bundles: Sequence[PromoCampaign], stackables: Sequence[PromoCampaign]
) -> None:
    """組合價（見模組說明第 6 點）：依活動 id，一組一組湊，湊不齊或不划算就換下一個活動。

    組合價活動勾了「可疊加」：組合價分攤完，每件再依序套上適用它的**可疊加**單件活動
    （打折／特價／折金額；2026-09-25 裁示）。不可疊加的活動一律不跟組合價併用。
    """
    group_no = 0
    for campaign in bundles:
        assert campaign.bundle_price is not None
        # 依「可以放進哪些格子」分堆，每堆由貴到便宜（依其他活動算完後的價格）。
        # 0 元品不進組合包（分不到折讓，還會被分到負數）。
        queues: dict[tuple[int, ...], deque[_Unit]] = {}
        for u in sorted(units, key=lambda u: -u.price):
            if u.claimed or u.list_price <= 0:
                continue
            fits = tuple(
                i
                for i, slot in enumerate(campaign.bundle_slots)
                if _slot_matches(campaign, slot, u.item)
            )
            if fits:
                queues.setdefault(fits, deque()).append(u)
        while True:
            picked = _pick_bundle(campaign, queues)
            if picked is None:
                break
            list_prices = [u.list_price for u in picked]
            shares = _allocate(
                sum(list_prices, Decimal(0)) - campaign.bundle_price,
                list_prices,
                [max(Decimal(0), p - _MIN_UNIT_PRICE) for p in list_prices],
            )
            # 每件成組後的價錢；組合價可疊加時連同疊上去的活動一起算，才拿去比划不划算。
            plans: list[tuple[_Unit, Decimal, list[tuple[int, Decimal]]]] = []
            for u, share in zip(picked, shares, strict=True):
                price = u.list_price - share
                allocations = [(campaign.id, share)] if share > 0 else []
                if campaign.stackable:
                    on_top = _chain(price, [c for c in stackables if campaign_applies(c, u.item)])
                    price = on_top.unit_price
                    allocations.extend(on_top.allocations)
                plans.append((u, price, allocations))
            if sum((p for _, p, _ in plans), Decimal(0)) >= sum(
                (u.price for u in picked), Decimal(0)
            ):
                break  # 最值錢的組合都不划算：這個活動不再成組
            for u, price, allocations in plans:
                u.claimed = True
                u.price = price
                u.allocations = allocations
                u.bundle = (group_no, campaign.id)
            group_no += 1


def _pick_bundle(
    campaign: PromoCampaign, queues: dict[tuple[int, ...], deque[_Unit]]
) -> list[_Unit] | None:
    """湊一組：每個位置配一件，且整組價值最高；湊不齊回 None。

    格子的範圍可以重疊。由貴到便宜逐件嘗試放進去（必要時讓已放好的件換到別格＝擴增路徑），
    放得進就留下，直到每個位置都有件。這是橫截擬陣上的貪婪法，挑出的正是價值最高的一組
    ——先湊出「隨便一組」再判斷划不划算，可能錯過真正划算的那組（Codex 審查）。

    放不進的件，同一堆（能放的格子完全相同）後面更便宜的也一定放不進（位置只會越來越滿），
    整堆跳過——否則同款很多件時每湊一組都要把它們重試一遍。
    """
    positions_of: dict[int, list[int]] = {}
    position_count = 0
    for i, slot in enumerate(campaign.bundle_slots):
        positions_of[i] = list(range(position_count, position_count + slot.qty))
        position_count += slot.qty
    holder: list[tuple[_Unit, tuple[int, ...]] | None] = [None] * position_count

    def place(u: _Unit, fits: tuple[int, ...], seen: set[int]) -> bool:
        for slot in fits:
            for position in positions_of[slot]:
                if position in seen:
                    continue
                seen.add(position)
                current = holder[position]
                if current is None or place(current[0], current[1], seen):
                    holder[position] = (u, fits)
                    return True
        return False

    failed: set[tuple[int, ...]] = set()
    filled = 0
    while filled < position_count:
        best: tuple[int, ...] | None = None
        for fits, queue in queues.items():
            if fits in failed:
                continue
            while queue and queue[0].claimed:
                queue.popleft()
            if queue and (best is None or queue[0].price > queues[best][0].price):
                best = fits
        if best is None:
            return None
        if place(queues[best][0], best, set()):
            queues[best].popleft()
            filled += 1
        else:
            failed.add(best)
    return [entry[0] for entry in holder if entry is not None]
