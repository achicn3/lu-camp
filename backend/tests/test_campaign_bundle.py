"""門市活動 v2 P4：組合價（純函式整車定價，docs/40 §4、§5 第 1 步；裁示 1、5、6、7）。

- 每個格子（範圍＋件數）都湊齊才成一組；可湊多組；湊齊自動套用。
- 只在比這些品項走其他活動更划算時才成組（對客人最划算）；成組的件不再參加其他活動。
- 組合價按組內各件原價比例分攤（最大餘數法，每件至少 1 元，加總＝組合價）。
- 寄售品不進組合包。
"""

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
