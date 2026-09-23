"""門市活動 v2 定價（純函式，docs/40 §5；2026-09-23 裁示）。

- 多個活動可同時生效；範圍可細到分類／品牌／型號／單件／一般商品／販售籃，可包含也可排除。
- 「不可疊加」＝不跟任何活動併用；「可疊加」的全部連乘（九折再九折＝81 折）。
- 候選＝各個不可疊加活動單用、以及全部可疊加活動連乘；取對客人最划算（單價最低）的。
- 每疊一個活動四捨五入一次（HALF_UP 整數元），各活動分到的折讓加總＝總折讓。
- 單價不低於 1 元（0 元是贈品，走贈品流程）。
"""

from decimal import Decimal

from app.modules.campaigns.pricing import (
    PromoCampaign,
    PromoItem,
    campaign_applies,
    price_unit,
)
from app.shared.enums import CampaignItemKind, CampaignTargetType

ALL_KINDS = frozenset(
    {CampaignItemKind.OWNED_SERIALIZED, CampaignItemKind.OWNED_BULK, CampaignItemKind.CATALOG}
)

TENT = PromoItem(
    kind=CampaignItemKind.OWNED_SERIALIZED,
    category_id=10,
    brand_id=20,
    product_model_id=30,
    serialized_item_id=40,
)


def campaign(
    cid: int,
    pct: int,
    *,
    stackable: bool = False,
    kinds: frozenset[CampaignItemKind] = ALL_KINDS,
    includes: tuple[tuple[CampaignTargetType, int], ...] = (),
    excludes: tuple[tuple[CampaignTargetType, int], ...] = (),
) -> PromoCampaign:
    return PromoCampaign(
        id=cid,
        name=f"活動{cid}",
        discount_pct=pct,
        stackable=stackable,
        item_kinds=kinds,
        includes=includes,
        excludes=excludes,
    )


def test_no_campaign_keeps_original_price() -> None:
    result = price_unit(Decimal(1000), TENT, [])
    assert result.unit_price == Decimal(1000)
    assert result.original_unit_price is None
    assert result.discount_per_unit == 0
    assert result.allocations == ()


def test_single_percent_off() -> None:
    result = price_unit(Decimal(1000), TENT, [campaign(1, 10)])
    assert result.unit_price == Decimal(900)
    assert result.original_unit_price == Decimal(1000)
    assert result.discount_per_unit == Decimal(100)
    assert result.allocations == ((1, Decimal(100)),)


def test_item_kind_flags_still_apply() -> None:
    consigned = PromoItem(kind=CampaignItemKind.CONSIGNMENT_SERIALIZED, serialized_item_id=1)
    assert not campaign_applies(campaign(1, 10), consigned)
    with_consignment = campaign(1, 10, kinds=ALL_KINDS | {CampaignItemKind.CONSIGNMENT_SERIALIZED})
    assert campaign_applies(with_consignment, consigned)


def test_include_targets_narrow_down_to_category_brand_model_or_item() -> None:
    for target in (
        (CampaignTargetType.CATEGORY, 10),
        (CampaignTargetType.BRAND, 20),
        (CampaignTargetType.PRODUCT_MODEL, 30),
        (CampaignTargetType.SERIALIZED_ITEM, 40),
    ):
        assert campaign_applies(campaign(1, 10, includes=(target,)), TENT), target
    assert not campaign_applies(
        campaign(1, 10, includes=((CampaignTargetType.CATEGORY, 99),)), TENT
    )


def test_any_include_is_enough_and_excludes_win() -> None:
    either = campaign(
        1,
        10,
        includes=((CampaignTargetType.CATEGORY, 99), (CampaignTargetType.BRAND, 20)),
    )
    assert campaign_applies(either, TENT)
    brand_but_not_this_one = campaign(
        1,
        10,
        includes=((CampaignTargetType.BRAND, 20),),
        excludes=((CampaignTargetType.SERIALIZED_ITEM, 40),),
    )
    assert not campaign_applies(brand_but_not_this_one, TENT)


def test_catalog_and_basket_targets() -> None:
    catalog = PromoItem(kind=CampaignItemKind.CATALOG, catalog_product_id=7, category_id=10)
    basket = PromoItem(kind=CampaignItemKind.OWNED_BULK, bulk_basket_id=8)
    assert campaign_applies(
        campaign(1, 10, includes=((CampaignTargetType.CATALOG_PRODUCT, 7),)), catalog
    )
    assert campaign_applies(
        campaign(1, 10, includes=((CampaignTargetType.BULK_BASKET, 8),)), basket
    )
    assert not campaign_applies(
        campaign(1, 10, includes=((CampaignTargetType.BULK_BASKET, 9),)), basket
    )


def test_stackable_campaigns_multiply_and_split_the_discount() -> None:
    result = price_unit(
        Decimal(1000), TENT, [campaign(1, 10, stackable=True), campaign(2, 10, stackable=True)]
    )
    assert result.unit_price == Decimal(810)
    assert result.allocations == ((1, Decimal(100)), (2, Decimal(90)))
    assert sum(a for _, a in result.allocations) == result.discount_per_unit


def test_non_stackable_is_never_combined_and_best_deal_wins() -> None:
    """不可疊加的七折 vs 可疊加的九折×九折（81 折）：取七折，而且不再疊。"""
    campaigns = [
        campaign(1, 10, stackable=True),
        campaign(2, 10, stackable=True),
        campaign(3, 30),
    ]
    result = price_unit(Decimal(1000), TENT, campaigns)
    assert result.unit_price == Decimal(700)
    assert result.allocations == ((3, Decimal(300)),)


def test_stack_wins_when_it_is_cheaper_than_any_exclusive() -> None:
    campaigns = [
        campaign(1, 20, stackable=True),
        campaign(2, 20, stackable=True),
        campaign(3, 30),
    ]
    result = price_unit(Decimal(1000), TENT, campaigns)
    assert result.unit_price == Decimal(640)  # 8 折 × 8 折
    assert [cid for cid, _ in result.allocations] == [1, 2]


def test_among_exclusives_the_biggest_discount_wins() -> None:
    result = price_unit(Decimal(1000), TENT, [campaign(1, 10), campaign(2, 25), campaign(3, 15)])
    assert result.allocations == ((2, Decimal(250)),)


def test_ties_are_deterministic_prefer_earliest_exclusive() -> None:
    a = price_unit(Decimal(1000), TENT, [campaign(2, 10), campaign(1, 10)])
    b = price_unit(Decimal(1000), TENT, [campaign(1, 10), campaign(2, 10)])
    assert a.allocations == b.allocations == ((1, Decimal(100)),)


def test_each_stacking_step_rounds_half_up() -> None:
    # 999 × 0.9 = 899.1 → 899；899 × 0.9 = 809.1 → 809
    result = price_unit(
        Decimal(999), TENT, [campaign(1, 10, stackable=True), campaign(2, 10, stackable=True)]
    )
    assert result.unit_price == Decimal(809)
    assert result.allocations == ((1, Decimal(100)), (2, Decimal(90)))


def test_stacking_order_is_by_campaign_id_not_input_order() -> None:
    a = price_unit(
        Decimal(999), TENT, [campaign(2, 30, stackable=True), campaign(1, 10, stackable=True)]
    )
    assert [cid for cid, _ in a.allocations] == [1, 2]


def test_price_never_drops_below_one_dollar() -> None:
    result = price_unit(Decimal(1), TENT, [campaign(1, 99)])
    assert result.unit_price == Decimal(1)
    assert result.allocations == ()
    assert result.original_unit_price is None


def test_campaigns_that_do_not_apply_are_ignored() -> None:
    other_brand = campaign(1, 50, includes=((CampaignTargetType.BRAND, 999),))
    result = price_unit(Decimal(1000), TENT, [other_brand, campaign(2, 10)])
    assert result.allocations == ((2, Decimal(100)),)
