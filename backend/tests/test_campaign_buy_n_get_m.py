"""門市活動 v2 P3：買 N 送 M（純函式整車定價，docs/40 §5 第 3 步；2026-09-23 裁示 3、4、7、9）。

- 適用的件依（第 2 步後的）單價由高到低排，每 N+M 件一組，組內最便宜的 M 件免費。
- 免費額**按組內各件價格比例分攤**到這 N+M 件（不是 0 元明細）；最大餘數法，加總不差一元。
- 寄售品不參加（裁示 7）；每件不低於 1 元。
- 可疊加的買 N 送 M 疊在第 2 步後的價格上（只限沒用到不可疊加活動的件）；
  不可疊加的＝不跟任何活動併用：跟第 2 步比，對客人比較划算才用（裁示 1、9）。
"""

from decimal import Decimal

import pytest

from app.modules.campaigns.pricing import (
    CartLine,
    PromoCampaign,
    PromoItem,
    price_cart,
    price_unit,
)
from app.shared.enums import CampaignItemKind, CampaignKind, CampaignTargetType
from app.shared.exceptions import SaleLineInvalid

KINDS = frozenset(
    {
        CampaignItemKind.OWNED_SERIALIZED,
        CampaignItemKind.OWNED_BULK,
        CampaignItemKind.CATALOG,
        CampaignItemKind.CONSIGNMENT_SERIALIZED,
    }
)


def item(n: int, kind: CampaignItemKind = CampaignItemKind.OWNED_SERIALIZED) -> PromoItem:
    return PromoItem(kind=kind, brand_id=1, serialized_item_id=n)


CANISTER = PromoItem(kind=CampaignItemKind.CATALOG, brand_id=1, catalog_product_id=7)


def bngm(
    cid: int,
    buy: int,
    free: int,
    *,
    stackable: bool = False,
    includes: tuple[tuple[CampaignTargetType, int], ...] = (),
) -> PromoCampaign:
    return PromoCampaign(
        id=cid,
        name=f"買{buy}送{free}",
        discount_pct=None,
        stackable=stackable,
        item_kinds=KINDS,
        includes=includes,
        kind=CampaignKind.BUY_N_GET_M,
        buy_qty=buy,
        free_qty=free,
    )


def pct(cid: int, value: int, *, stackable: bool = False) -> PromoCampaign:
    return PromoCampaign(
        id=cid, name=f"{value}%off", discount_pct=value, stackable=stackable, item_kinds=KINDS
    )


def line(price: int, it: PromoItem | None, qty: int = 1) -> CartLine:
    return CartLine(item=it, unit_price=Decimal(price), qty=qty)


def test_free_amount_is_allocated_by_price_across_the_group() -> None:
    """買二送一：1000、600、400 → 送 400，按比例分到三件：200／120／80。"""
    result = price_cart(
        [line(1000, item(1)), line(600, item(2)), line(400, item(3))], [bngm(9, 2, 1)]
    )
    assert [r.line_total for r in result] == [Decimal(800), Decimal(480), Decimal(320)]
    assert [r.allocations for r in result] == [
        ((9, Decimal(200)),),
        ((9, Decimal(120)),),
        ((9, Decimal(80)),),
    ]
    assert [r.discount_amount for r in result] == [Decimal(200), Decimal(120), Decimal(80)]
    assert all(r.original_unit_price is not None for r in result)


def test_cheapest_is_free_regardless_of_cart_order() -> None:
    result = price_cart(
        [line(400, item(3)), line(1000, item(1)), line(600, item(2))], [bngm(9, 2, 1)]
    )
    assert sum(r.line_total for r in result) == Decimal(1600)
    assert [r.free_units for r in result] == [1, 0, 0]


def test_same_line_quantity_forms_groups_and_splits_without_losing_a_dollar() -> None:
    """同一行 3 罐 100 元買二送一：送 100 分到 3 件（34/33/33），本行 200、平均單價 67。"""
    [result] = price_cart([line(100, CANISTER, qty=3)], [bngm(9, 2, 1)])
    assert result.line_total == Decimal(200)
    assert result.allocations == ((9, Decimal(100)),)
    assert result.unit_price == Decimal(67)
    assert result.original_unit_price == Decimal(100)
    assert result.free_units == 1


def test_leftover_units_that_do_not_fill_a_group_pay_full_price() -> None:
    """4 件買二送一：最貴三件成一組（送 300），最便宜的 200 湊不成組、原價。"""
    result = price_cart(
        [line(500, item(1)), line(400, item(2)), line(300, item(3)), line(200, item(4))],
        [bngm(9, 2, 1)],
    )
    assert result[3].line_total == Decimal(200)
    assert result[3].allocations == ()
    assert result[3].original_unit_price is None
    assert sum(r.line_total for r in result[:3]) == Decimal(900)


def test_not_enough_items_means_no_discount() -> None:
    result = price_cart([line(500, item(1)), line(400, item(2))], [bngm(9, 2, 1)])
    assert [r.line_total for r in result] == [Decimal(500), Decimal(400)]
    assert all(r.allocations == () for r in result)


def test_consignment_never_joins_buy_n_get_m() -> None:
    consigned = item(3, CampaignItemKind.CONSIGNMENT_SERIALIZED)
    result = price_cart(
        [line(1000, item(1)), line(600, item(2)), line(400, consigned)], [bngm(9, 2, 1)]
    )
    assert [r.line_total for r in result] == [Decimal(1000), Decimal(600), Decimal(400)]


def test_lines_without_promo_item_are_left_alone() -> None:
    """贈品、餐飲、寄售散裝（item=None）不參加。"""
    result = price_cart([line(1000, item(1)), line(600, None), line(400, item(2))], [bngm(9, 1, 1)])
    assert result[1].line_total == Decimal(600)
    assert result[1].allocations == ()
    assert result[0].line_total + result[2].line_total == Decimal(1000)


def test_scope_limits_which_items_count() -> None:
    other_brand = PromoItem(kind=CampaignItemKind.OWNED_SERIALIZED, brand_id=2)
    only_brand_1 = bngm(9, 1, 1, includes=((CampaignTargetType.BRAND, 1),))
    result = price_cart(
        [line(1000, item(1)), line(900, other_brand), line(400, item(2))], [only_brand_1]
    )
    assert result[1].line_total == Decimal(900)
    assert result[0].line_total + result[2].line_total == Decimal(1000)


def test_no_unit_drops_below_one_dollar() -> None:
    result = price_cart([line(1, item(1)), line(1, item(2))], [bngm(9, 1, 1)])
    assert all(r.line_total >= 1 for r in result)


def test_allocations_always_sum_to_line_discount() -> None:
    result = price_cart(
        [line(999, item(1)), line(333, item(2)), line(101, item(3)), line(77, CANISTER, qty=4)],
        [bngm(9, 2, 1)],
    )
    for r in result:
        assert sum(a for _, a in r.allocations) == r.discount_amount
        assert r.line_total == r.list_total - r.discount_amount


def test_stackable_buy_n_get_m_applies_on_top_of_stackable_discounts() -> None:
    """九折（可疊加）＋買二送一（可疊加）：三件 1000 → 先九折成 900，再送一件 900 分攤。"""
    result = price_cart(
        [line(1000, item(n)) for n in (1, 2, 3)],
        [pct(1, 10, stackable=True), bngm(9, 2, 1, stackable=True)],
    )
    assert sum(r.line_total for r in result) == Decimal(1800)
    assert result[0].allocations == ((1, Decimal(100)), (9, Decimal(300)))


def test_stackable_buy_n_get_m_skips_items_priced_by_a_non_stackable_campaign() -> None:
    """不可疊加＝不跟任何活動併用：已用不可疊加八折的件，不再參加可疊加的買 N 送 M。"""
    result = price_cart(
        [line(1000, item(n)) for n in (1, 2, 3)],
        [pct(1, 20), bngm(9, 2, 1, stackable=True)],
    )
    assert [r.line_total for r in result] == [Decimal(800)] * 3
    assert all(r.allocations == ((1, Decimal(200)),) for r in result)


def test_non_stackable_buy_n_get_m_replaces_weaker_discount() -> None:
    """不可疊加的買二送一 vs 不可疊加九折：買二送一只付 2000 < 九折 2700 → 用買二送一、九折拿掉。"""
    result = price_cart([line(1000, item(n)) for n in (1, 2, 3)], [pct(1, 10), bngm(9, 2, 1)])
    assert sum(r.line_total for r in result) == Decimal(2000)
    assert all([cid for cid, _ in r.allocations] == [9] for r in result)


def test_non_stackable_buy_n_get_m_loses_to_a_better_discount() -> None:
    """買五送一（六件付五件）vs 五折：五折比較划算 → 維持五折。"""
    result = price_cart([line(100, CANISTER, qty=6)], [pct(1, 50), bngm(9, 5, 1)])
    assert result[0].line_total == Decimal(300)
    assert result[0].allocations == ((1, Decimal(300)),)
    assert result[0].free_units == 0


def test_each_unit_joins_at_most_one_buy_n_get_m() -> None:
    result = price_cart(
        [line(100, CANISTER, qty=4)], [bngm(8, 1, 1), bngm(9, 1, 1, stackable=True)]
    )
    assert result[0].line_total == Decimal(200)
    assert [cid for cid, _ in result[0].allocations] == [8]


def test_price_unit_ignores_buy_n_get_m_campaigns() -> None:
    result = price_unit(Decimal(1000), item(1), [bngm(9, 1, 1)])
    assert result.unit_price == Decimal(1000)
    assert result.allocations == ()


def test_cart_without_buy_n_get_m_matches_per_unit_pricing() -> None:
    campaigns = [pct(1, 10), pct(2, 15, stackable=True), pct(3, 10, stackable=True)]
    [result] = price_cart([line(1000, CANISTER, qty=2)], campaigns)
    single = price_unit(Decimal(1000), CANISTER, campaigns)
    assert result.line_total == single.unit_price * 2
    assert result.allocations == tuple((cid, a * 2) for cid, a in single.allocations)


def test_zero_share_is_not_recorded_as_an_allocation() -> None:
    """1000＋10 買一送一：送的 10 元分成 10／0；0 元不記成活動明細（Codex 審查）。"""
    result = price_cart([line(1000, item(1)), line(10, item(2))], [bngm(9, 1, 1)])
    assert [r.line_total for r in result] == [Decimal(990), Decimal(10)]
    assert result[0].allocations == ((9, Decimal(10)),)
    assert result[1].allocations == ()
    assert result[1].original_unit_price is None
    assert all(a > 0 for r in result for _, a in r.allocations)


def test_large_quantity_without_buy_n_get_m_is_priced_per_line() -> None:
    """沒有買 N 送 M 適用時不逐件展開：數量再大也只是乘法（Codex 審查）。"""
    [result] = price_cart([line(100, CANISTER, qty=10**9)], [pct(1, 10)])
    assert result.line_total == Decimal(90) * 10**9
    assert result.allocations == ((1, Decimal(10) * 10**9),)


def test_absurd_quantity_under_buy_n_get_m_is_rejected() -> None:
    with pytest.raises(SaleLineInvalid):
        price_cart([line(100, CANISTER, qty=10**9)], [bngm(9, 5, 1)])


def test_zero_priced_items_do_not_break_the_cart() -> None:
    """0 元品（收購允許）成組時不能除以零；混了一般價的組照常算（Codex 審查）。"""
    zeros = price_cart([line(0, CANISTER, qty=2)], [bngm(9, 1, 1)])
    assert zeros[0].line_total == 0
    assert zeros[0].allocations == ()
    mixed = price_cart([line(0, item(1)), line(0, item(2)), line(500, item(3))], [bngm(9, 2, 1)])
    assert [r.line_total for r in mixed] == [Decimal(0), Decimal(0), Decimal(500)]


# ── P3b：店員改指定送哪件（裁示 4）────────────────────────────────


def chosen(price: int, it: PromoItem | None, qty: int = 1) -> CartLine:
    return CartLine(item=it, unit_price=Decimal(price), qty=qty, free_requested=True)


def test_clerk_can_choose_which_item_is_free() -> None:
    """買二送一 1000／600／400，指定送 600：送 600，按比例分攤（300／180／120）。"""
    result = price_cart(
        [line(1000, item(1)), chosen(600, item(2)), line(400, item(3))], [bngm(9, 2, 1)]
    )
    assert [r.line_total for r in result] == [Decimal(700), Decimal(420), Decimal(280)]
    assert [r.free_units for r in result] == [0, 1, 0]
    assert result[1].free_campaign_id == 9


def test_choosing_a_leftover_item_swaps_it_into_the_last_group() -> None:
    """4 件買二送一：預設 500/400/300 成組送 300、200 落單；指定送 200 → 200 進組、300 改原價。"""
    result = price_cart(
        [line(500, item(1)), line(400, item(2)), line(300, item(3)), chosen(200, item(4))],
        [bngm(9, 2, 1)],
    )
    assert [r.free_units for r in result] == [0, 0, 0, 1]
    assert result[2].line_total == Decimal(300)
    assert result[2].allocations == ()
    assert sum(r.line_total for r in result) == Decimal(1200)


def test_choosing_more_items_than_free_slots_keeps_the_first_ones() -> None:
    result = price_cart(
        [chosen(1000, item(1)), chosen(600, item(2)), line(400, item(3))], [bngm(9, 2, 1)]
    )
    assert [r.free_units for r in result] == [1, 0, 0]
    assert sum(r.line_total for r in result) == Decimal(1000)


def test_choosing_an_item_outside_the_campaign_does_nothing() -> None:
    consigned = item(4, CampaignItemKind.CONSIGNMENT_SERIALIZED)
    result = price_cart(
        [line(1000, item(1)), line(600, item(2)), line(400, item(3)), chosen(900, consigned)],
        [bngm(9, 2, 1)],
    )
    assert [r.free_units for r in result] == [0, 0, 1, 0]
    assert result[3].line_total == Decimal(900)


def test_default_free_line_reports_its_campaign() -> None:
    result = price_cart(
        [line(1000, item(1)), line(600, item(2)), line(400, item(3))], [bngm(9, 2, 1)]
    )
    assert [r.free_campaign_id for r in result] == [None, None, 9]


def test_choosing_one_line_of_several_units_frees_only_as_many_as_slots() -> None:
    """同一行 3 件都想送，但買二送一 3 件只有 1 個送的位置。"""
    [result] = price_cart([chosen(100, CANISTER, qty=3)], [bngm(9, 2, 1)])
    assert result.free_units == 1
    assert result.line_total == Decimal(200)


def test_expensive_choice_wins_even_over_a_chosen_default_free_item() -> None:
    """1000 與 400 都指定送、只有一個位置：依排序取貴的 1000（Codex 審查）。"""
    result = price_cart(
        [chosen(1000, item(1)), line(600, item(2)), chosen(400, item(3))], [bngm(9, 2, 1)]
    )
    assert [r.free_units for r in result] == [1, 0, 0]
    assert sum(r.line_total for r in result) == Decimal(1000)


def test_choice_moves_to_another_group_when_its_own_slot_is_taken() -> None:
    """兩組買一送一（1000,900｜500,400），指定送 1000 與 900：各佔一組送的位置。"""
    result = price_cart(
        [chosen(1000, item(1)), chosen(900, item(2)), line(500, item(3)), line(400, item(4))],
        [bngm(9, 1, 1)],
    )
    assert [r.free_units for r in result] == [1, 1, 0, 0]
    assert sum(r.line_total for r in result) == Decimal(900)


def test_leftover_line_is_reported_as_choosable() -> None:
    result = price_cart(
        [line(500, item(1)), line(400, item(2)), line(300, item(3)), line(200, item(4))],
        [bngm(9, 2, 1)],
    )
    assert [r.buy_n_get_m_eligible for r in result] == [True, True, True, True]
    assert result[3].buy_n_get_m_units == 0
    other = price_cart([line(200, None)], [bngm(9, 2, 1)])
    assert other[0].buy_n_get_m_eligible is False
