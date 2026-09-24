"""門市活動 v2 P4：組合價（純函式整車定價，docs/40 §4、§5 第 1 步；裁示 1、5、6、7）。

- 每個格子（範圍＋件數）都湊齊才成一組；可湊多組；湊齊自動套用。
- 只在比這些品項走其他活動更划算時才成組（對客人最划算）；成組的件不再參加其他活動。
- 組合價按組內各件原價比例分攤（最大餘數法，每件至少 1 元，加總＝組合價）。
- 寄售品不進組合包。
"""

import time
from decimal import Decimal

from app.modules.campaigns.pricing import (
    BundleSlot,
    CartLine,
    PromoCampaign,
    PromoItem,
    price_cart,
)
from app.shared.enums import CampaignItemKind, CampaignKind, CampaignTargetType

KINDS = frozenset(
    {
        CampaignItemKind.OWNED_SERIALIZED,
        CampaignItemKind.OWNED_BULK,
        CampaignItemKind.CATALOG,
        CampaignItemKind.CONSIGNMENT_SERIALIZED,
    }
)
TENT_MODEL, CHAIR_MODEL, GAS = 10, 20, 30


def tent(n: int) -> PromoItem:
    return PromoItem(
        kind=CampaignItemKind.OWNED_SERIALIZED, product_model_id=TENT_MODEL, serialized_item_id=n
    )


def chair(n: int, kind: CampaignItemKind = CampaignItemKind.OWNED_SERIALIZED) -> PromoItem:
    return PromoItem(kind=kind, product_model_id=CHAIR_MODEL, serialized_item_id=n)


CANISTER = PromoItem(kind=CampaignItemKind.CATALOG, catalog_product_id=GAS)


def bundle(cid: int, price: int, *slots: tuple[CampaignTargetType, int, int]) -> PromoCampaign:
    return PromoCampaign(
        id=cid,
        name=f"組合{cid}",
        discount_pct=None,
        stackable=False,
        item_kinds=KINDS,
        kind=CampaignKind.BUNDLE,
        bundle_price=Decimal(price),
        bundle_slots=tuple(BundleSlot(qty=q, includes=((t, i),)) for t, i, q in slots),
    )


def pct(cid: int, value: int) -> PromoCampaign:
    return PromoCampaign(
        id=cid, name=f"{value}%off", discount_pct=value, stackable=False, item_kinds=KINDS
    )


TENT_CHAIR = (
    (CampaignTargetType.PRODUCT_MODEL, TENT_MODEL, 1),
    (CampaignTargetType.PRODUCT_MODEL, CHAIR_MODEL, 1),
)


def line(price: int, it: PromoItem | None, qty: int = 1) -> CartLine:
    return CartLine(item=it, unit_price=Decimal(price), qty=qty)


def test_full_set_gets_bundle_price_split_by_original_price() -> None:
    """帳篷 6000＋椅子 2000 組合價 7000：折 1000 按原價比例（750／250）。"""
    result = price_cart([line(6000, tent(1)), line(2000, chair(2))], [bundle(5, 7000, *TENT_CHAIR)])
    assert [r.line_total for r in result] == [Decimal(5250), Decimal(1750)]
    assert [r.allocations for r in result] == [((5, Decimal(750)),), ((5, Decimal(250)),)]
    assert [r.bundle_groups for r in result] == [((0, 5, 1),), ((0, 5, 1),)]


def test_incomplete_set_is_not_bundled() -> None:
    result = price_cart([line(6000, tent(1))], [bundle(5, 7000, *TENT_CHAIR)])
    assert result[0].line_total == Decimal(6000)
    assert result[0].bundle_groups == ()


def test_multiple_sets_form_multiple_groups_most_expensive_first() -> None:
    result = price_cart(
        [line(6000, tent(1)), line(5000, tent(2)), line(2000, chair(3)), line(1500, chair(4))],
        [bundle(5, 6000, *TENT_CHAIR)],
    )
    assert [r.bundle_groups for r in result] == [
        ((0, 5, 1),),
        ((1, 5, 1),),
        ((0, 5, 1),),
        ((1, 5, 1),),
    ]
    assert result[0].line_total + result[2].line_total == Decimal(6000)
    assert result[1].line_total + result[3].line_total == Decimal(6000)


def test_bundle_only_when_cheaper_than_other_campaigns() -> None:
    """全館五折（4000）比組合價 7000 划算 → 不成組，照五折。"""
    result = price_cart(
        [line(6000, tent(1)), line(2000, chair(2))], [pct(1, 50), bundle(5, 7000, *TENT_CHAIR)]
    )
    assert [r.line_total for r in result] == [Decimal(3000), Decimal(1000)]
    assert all(r.bundle_groups == () for r in result)


def test_bundled_items_do_not_take_other_campaigns() -> None:
    """九折（7200）不如組合價 7000 → 成組，九折不再疊上去。"""
    result = price_cart(
        [line(6000, tent(1)), line(2000, chair(2))], [pct(1, 10), bundle(5, 7000, *TENT_CHAIR)]
    )
    assert sum(r.line_total for r in result) == Decimal(7000)
    assert all([cid for cid, _ in r.allocations] == [5] for r in result)


def test_consignment_never_joins_a_bundle() -> None:
    consigned = chair(2, CampaignItemKind.CONSIGNMENT_SERIALIZED)
    result = price_cart(
        [line(6000, tent(1)), line(2000, consigned)], [bundle(5, 7000, *TENT_CHAIR)]
    )
    assert [r.line_total for r in result] == [Decimal(6000), Decimal(2000)]


def test_slot_quantity_can_come_from_one_line() -> None:
    """帳篷＋瓦斯 2 罐 組合價 6100；購物車 3 罐：2 罐進組、1 罐原價。"""
    offer = bundle(
        5,
        6100,
        (CampaignTargetType.PRODUCT_MODEL, TENT_MODEL, 1),
        (CampaignTargetType.CATALOG_PRODUCT, GAS, 2),
    )
    result = price_cart([line(6000, tent(1)), line(100, CANISTER, qty=3)], [offer])
    assert result[1].bundle_groups == ((0, 5, 2),)
    assert result[0].line_total + result[1].line_total == Decimal(6100 + 100)
    assert sum(a for _, a in result[1].allocations) == result[1].discount_amount


def test_each_item_pays_at_least_one_dollar_and_totals_match() -> None:
    offer = bundle(
        5,
        3,
        (CampaignTargetType.PRODUCT_MODEL, TENT_MODEL, 1),
        (CampaignTargetType.CATALOG_PRODUCT, GAS, 2),
    )
    result = price_cart([line(9000, tent(1)), line(10, CANISTER, qty=2)], [offer])
    assert result[0].line_total + result[1].line_total == Decimal(3)
    assert result[1].line_total >= 2


def test_bundled_units_are_left_out_of_buy_n_get_m() -> None:
    bngm = PromoCampaign(
        id=9,
        name="買一送一",
        discount_pct=None,
        stackable=False,
        item_kinds=KINDS,
        kind=CampaignKind.BUY_N_GET_M,
        buy_qty=1,
        free_qty=1,
    )
    offer = bundle(
        5,
        6000,
        (CampaignTargetType.PRODUCT_MODEL, TENT_MODEL, 1),
        (CampaignTargetType.CATALOG_PRODUCT, GAS, 1),
    )
    result = price_cart([line(6000, tent(1)), line(100, CANISTER, qty=3)], [offer, bngm])
    assert result[1].bundle_groups == ((0, 5, 1),)
    # 組合 6000＋剩下 2 罐買一送一（付 100）
    assert sum(r.line_total for r in result) == Decimal(6100)
    assert result[1].free_units == 1


def test_bundle_only_lines_are_not_offered_free_item_choice() -> None:
    """只有組合價在進行：這些行不該出現「改送這件」（那是買 N 送 M 的功能）。"""
    result = price_cart([line(6000, tent(1)), line(2000, chair(2))], [bundle(5, 7000, *TENT_CHAIR)])
    assert [r.buy_n_get_m_eligible for r in result] == [False, False]


def test_overlapping_slots_find_a_valid_assignment() -> None:
    """第 1 格收 A 或 B、第 2 格只收 A：A 要留給第 2 格，B 放第 1 格才湊得出（Codex 審查）。"""
    a = PromoItem(kind=CampaignItemKind.OWNED_SERIALIZED, product_model_id=1, serialized_item_id=1)
    b = PromoItem(kind=CampaignItemKind.OWNED_SERIALIZED, product_model_id=2, serialized_item_id=2)
    offer = PromoCampaign(
        id=5,
        name="重疊格",
        discount_pct=None,
        stackable=False,
        item_kinds=KINDS,
        kind=CampaignKind.BUNDLE,
        bundle_price=Decimal(150),
        bundle_slots=(
            BundleSlot(
                qty=1,
                includes=(
                    (CampaignTargetType.PRODUCT_MODEL, 1),
                    (CampaignTargetType.PRODUCT_MODEL, 2),
                ),
            ),
            BundleSlot(qty=1, includes=((CampaignTargetType.PRODUCT_MODEL, 1),)),
        ),
    )
    result = price_cart([line(200, a), line(100, b)], [offer])
    assert sum(r.line_total for r in result) == Decimal(150)
    assert all(r.bundle_groups for r in result)


def _model(n: int) -> PromoItem:
    return PromoItem(
        kind=CampaignItemKind.OWNED_SERIALIZED, product_model_id=n, serialized_item_id=n
    )


def _slot(*models: int) -> BundleSlot:
    return BundleSlot(qty=1, includes=tuple((CampaignTargetType.PRODUCT_MODEL, m) for m in models))


def _custom_bundle(price: int, *slots: BundleSlot) -> PromoCampaign:
    return PromoCampaign(
        id=5,
        name="自訂組",
        discount_pct=None,
        stackable=False,
        item_kinds=KINDS,
        kind=CampaignKind.BUNDLE,
        bundle_price=Decimal(price),
        bundle_slots=slots,
    )


def test_overlapping_slots_pick_the_most_valuable_combination() -> None:
    """A=100、B=90、C=1；格子 {A,C}、{A,B}：該配 A+B（190＞150 才成組），不是 C+A（Codex 審查）。"""
    cart = [line(100, _model(1)), line(90, _model(2)), line(1, _model(3))]
    for slots in [(_slot(1, 3), _slot(1, 2)), (_slot(1, 2), _slot(1, 3))]:
        result = price_cart(cart, [_custom_bundle(150, *slots)])
        assert sum(r.line_total for r in result) == Decimal(151), slots
        assert result[2].bundle_groups == ()


def test_zero_priced_items_never_join_a_bundle() -> None:
    """0 元品（收購允許）不進組合包：否則會被分到負的折讓、比原價還貴（Codex 審查）。"""
    result = price_cart(
        [line(0, _model(1)), line(100, _model(2))], [_custom_bundle(50, _slot(1), _slot(2))]
    )
    assert [r.line_total for r in result] == [Decimal(0), Decimal(100)]
    assert all(r.discount_amount >= 0 for r in result)


def test_bundle_matching_scales_to_the_unit_limit() -> None:
    """上限 1 萬件（同款各 5000）：不可每湊一組就把同款重試一遍（曾經要 4.8 秒）。"""
    other = PromoItem(kind=CampaignItemKind.CATALOG, catalog_product_id=GAS + 1)
    offer = bundle(
        5,
        150,
        (CampaignTargetType.CATALOG_PRODUCT, GAS, 1),
        (CampaignTargetType.CATALOG_PRODUCT, GAS + 1, 1),
    )
    started = time.perf_counter()
    result = price_cart([line(100, CANISTER, qty=5000), line(100, other, qty=5000)], [offer])
    assert time.perf_counter() - started < 1.5
    assert len(result[0].bundle_groups) == 5000
    assert sum(r.line_total for r in result) == Decimal(150 * 5000)


def _stackable(c: PromoCampaign) -> PromoCampaign:
    return PromoCampaign(**{**c.__dict__, "stackable": True})


def test_stackable_bundle_takes_stackable_campaigns_on_top() -> None:
    """組合價勾「可疊加」（2026-09-25 裁示）：組合價算完再套可疊加的活動（7000 再九折＝6300）。"""
    storewide = PromoCampaign(
        id=1, name="全館九折", discount_pct=10, stackable=True, item_kinds=KINDS
    )
    offer = _stackable(bundle(5, 7000, *TENT_CHAIR))
    result = price_cart([line(6000, tent(1)), line(2000, chair(2))], [storewide, offer])
    assert [r.line_total for r in result] == [Decimal(4725), Decimal(1575)]
    assert result[0].allocations == ((5, Decimal(750)), (1, Decimal(525)))


def test_non_stackable_campaigns_never_join_a_stackable_bundle() -> None:
    other = pct(1, 10)
    offer = _stackable(bundle(5, 7000, *TENT_CHAIR))
    result = price_cart([line(6000, tent(1)), line(2000, chair(2))], [other, offer])
    assert sum(r.line_total for r in result) == Decimal(7000)


def test_bundle_not_marked_stackable_stays_at_bundle_price() -> None:
    storewide = PromoCampaign(
        id=1, name="全館九折", discount_pct=10, stackable=True, item_kinds=KINDS
    )
    result = price_cart(
        [line(6000, tent(1)), line(2000, chair(2))], [storewide, bundle(5, 7000, *TENT_CHAIR)]
    )
    assert sum(r.line_total for r in result) == Decimal(7000)
