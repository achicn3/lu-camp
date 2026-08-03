"""臨時折扣的定價與分攤（純函式，無 DB）。

分攤規則寫在這裡而不是散在 service 裡，是因為**退貨要依當初的分攤結果退款**——
規則一旦模糊，部分退貨就會多退或少退。純函式讓每條規則都能被單獨證偽。
"""

from decimal import Decimal

import pytest

from app.modules.sales.pricing import DiscountRequest, PricingLine, apply_discounts
from app.shared.enums import AdjustmentScope, CalculationMethod, SaleLineKind
from app.shared.exceptions import InvalidDiscount


def _normal(key: str, amount: str, *, discountable: bool = True) -> PricingLine:
    return PricingLine(
        key=key,
        kind=SaleLineKind.NORMAL,
        line_total=Decimal(amount),
        discountable=discountable,
    )


def _gift(key: str, retail: str) -> PricingLine:
    return PricingLine(
        key=key,
        kind=SaleLineKind.GIFT,
        line_total=Decimal(0),
        discountable=False,
        gift_retail_value=Decimal(retail),
    )


def _item(key: str, method: CalculationMethod, value: str) -> DiscountRequest:
    return DiscountRequest(
        scope=AdjustmentScope.ITEM, method=method, value=Decimal(value), target_key=key
    )


def _order(method: CalculationMethod, value: str) -> DiscountRequest:
    return DiscountRequest(scope=AdjustmentScope.ORDER, method=method, value=Decimal(value))


# ── 無折扣 ──────────────────────────────────────────────────────────────────


def test_no_discount_leaves_every_line_at_its_full_amount() -> None:
    result = apply_discounts([_normal("a", "1000"), _normal("b", "500")], [])
    assert result.net_by_line == {"a": Decimal(1000), "b": Decimal(500)}
    assert result.net_amount == Decimal(1500)
    assert result.gross_amount == Decimal(1500)
    assert result.total_discount_amount == Decimal(0)


# ── 單品折扣 ────────────────────────────────────────────────────────────────


def test_fixed_amount_item_discount_only_touches_that_line() -> None:
    result = apply_discounts(
        [_normal("a", "1000"), _normal("b", "500")],
        [_item("a", CalculationMethod.FIXED_AMOUNT, "100")],
    )
    assert result.net_by_line == {"a": Decimal(900), "b": Decimal(500)}
    assert result.item_discount_amount == Decimal(100)
    assert result.order_discount_amount == Decimal(0)
    assert result.net_amount == Decimal(1400)


def test_percentage_item_discount_rounds_to_whole_dollars() -> None:
    """333 的 15% ＝ 49.95 → 四捨五入 50（金額一律整數元）。"""
    result = apply_discounts(
        [_normal("a", "333")], [_item("a", CalculationMethod.PERCENTAGE, "15")]
    )
    assert result.item_discount_amount == Decimal(50)
    assert result.net_by_line == {"a": Decimal(283)}


def test_item_discount_may_not_exceed_the_line_amount() -> None:
    with pytest.raises(InvalidDiscount, match="超過"):
        apply_discounts([_normal("a", "100")], [_item("a", CalculationMethod.FIXED_AMOUNT, "101")])


def test_item_discount_may_not_zero_a_line_out() -> None:
    """折到 0 元＝變相贈品。要免費請用贈品，否則贈品報表統計不到。

    這正是需求書第一條原則：贈品不可實作成 100% 折扣。
    """
    with pytest.raises(InvalidDiscount, match="贈品"):
        apply_discounts([_normal("a", "100")], [_item("a", CalculationMethod.FIXED_AMOUNT, "100")])


def test_item_discount_target_must_exist_and_be_discountable() -> None:
    with pytest.raises(InvalidDiscount, match="不存在"):
        apply_discounts([_normal("a", "100")], [_item("zzz", CalculationMethod.FIXED_AMOUNT, "10")])
    with pytest.raises(InvalidDiscount, match="不可折扣"):
        apply_discounts(
            [_normal("a", "100", discountable=False)],
            [_item("a", CalculationMethod.FIXED_AMOUNT, "10")],
        )


# ── 整單折扣的分攤 ──────────────────────────────────────────────────────────


def test_order_discount_is_allocated_in_proportion_to_line_amounts() -> None:
    result = apply_discounts(
        [_normal("a", "600"), _normal("b", "400")], [_order(CalculationMethod.FIXED_AMOUNT, "100")]
    )
    assert result.order_discount_amount == Decimal(100)
    assert result.manual_discount_by_line == {"a": Decimal(60), "b": Decimal(40)}
    assert result.net_by_line == {"a": Decimal(540), "b": Decimal(360)}
    assert result.net_amount == Decimal(900)


def test_rounding_remainder_is_distributed_by_largest_remainder() -> None:
    """三行各 100，整單折 10：各得 3.33 → 3,3,3，剩下的 1 元發給小數最大者（同分取前者）。

    尾差必須有**固定歸屬**，否則同一筆訂單重算兩次可能得到不同分攤，退貨就會對不上。
    """
    result = apply_discounts(
        [_normal("a", "100"), _normal("b", "100"), _normal("c", "100")],
        [_order(CalculationMethod.FIXED_AMOUNT, "10")],
    )
    assert result.manual_discount_by_line == {"a": Decimal(4), "b": Decimal(3), "c": Decimal(3)}
    assert sum(result.manual_discount_by_line.values()) == Decimal(10)
    assert result.net_amount == Decimal(290)


def test_allocation_is_never_negative_and_never_raises_a_line_above_its_amount() -> None:
    """Codex 對抗審查（2026-08-03，high）：先四捨五入再讓最後一行吃差額會超發。

    51、51、51、47 分攤 2 元，舊寫法得到 1、1、1、**−1**——最後一行的實付從 47 變成 48，
    比原價還高。分攤必須每筆非負、總和精確等於折扣。
    """
    result = apply_discounts(
        [_normal("a", "51"), _normal("b", "51"), _normal("c", "51"), _normal("d", "47")],
        [_order(CalculationMethod.FIXED_AMOUNT, "2")],
    )
    shares = result.manual_discount_by_line
    assert all(share >= 0 for share in shares.values()), shares
    assert sum(shares.values()) == Decimal(2)
    # 沒有任何一行的實付被推高到原金額之上
    assert result.net_by_line["d"] <= Decimal(47)
    assert result.net_amount == Decimal(200 - 2)


def test_allocation_stays_non_negative_across_many_small_lines() -> None:
    """性質測試：各種「多筆小額 × 小折扣」組合都不得出現負分攤或總和不符。"""
    for amounts in (
        ["51", "51", "51", "47"],
        ["33", "33", "33", "1"],
        ["7", "7", "7", "7", "7"],
        ["999", "1", "1"],
    ):
        for discount in ("1", "2", "3", "7"):
            lines = [_normal(str(i), value) for i, value in enumerate(amounts)]
            result = apply_discounts(lines, [_order(CalculationMethod.FIXED_AMOUNT, discount)])
            shares = result.manual_discount_by_line
            assert all(share >= 0 for share in shares.values()), (amounts, discount, shares)
            assert sum(shares.values()) == Decimal(discount), (amounts, discount, shares)
            for key, line in zip([str(i) for i in range(len(amounts))], lines, strict=True):
                assert result.net_by_line[key] <= line.line_total


def test_order_discount_excludes_gifts() -> None:
    """贈品不參與分攤——它本來就沒有金額可折，且贈品價值不得混入折扣。"""
    result = apply_discounts(
        [_normal("a", "500"), _gift("g", "300")], [_order(CalculationMethod.FIXED_AMOUNT, "100")]
    )
    assert result.manual_discount_by_line == {"a": Decimal(100)}
    assert result.net_by_line == {"a": Decimal(400), "g": Decimal(0)}
    assert result.gift_retail_value == Decimal(300)
    # 贈品價值不進折扣總額，也不進應付
    assert result.total_discount_amount == Decimal(100)
    assert result.net_amount == Decimal(400)


def test_order_discount_excludes_non_discountable_lines() -> None:
    """寄售與餐飲不可折——分攤時要跳過，不能讓它們吸收折扣。"""
    result = apply_discounts(
        [_normal("a", "500"), _normal("consign", "500", discountable=False)],
        [_order(CalculationMethod.PERCENTAGE, "10")],
    )
    # 基礎只有可折的 500 → 折 50，全落在 a
    assert result.order_discount_amount == Decimal(50)
    assert result.manual_discount_by_line == {"a": Decimal(50)}
    assert result.net_by_line == {"a": Decimal(450), "consign": Decimal(500)}


def test_order_percentage_is_computed_on_the_discountable_base_only() -> None:
    result = apply_discounts(
        [_normal("a", "1000"), _normal("x", "1000", discountable=False)],
        [_order(CalculationMethod.PERCENTAGE, "20")],
    )
    assert result.order_discount_amount == Decimal(200)  # 只算可折的 1000


def test_order_discount_may_not_zero_the_order_out() -> None:
    with pytest.raises(InvalidDiscount, match="贈品"):
        apply_discounts([_normal("a", "500")], [_order(CalculationMethod.FIXED_AMOUNT, "500")])


def test_order_discount_may_not_exceed_the_discountable_base() -> None:
    with pytest.raises(InvalidDiscount, match="超過"):
        apply_discounts([_normal("a", "500")], [_order(CalculationMethod.FIXED_AMOUNT, "501")])


def test_order_discount_with_no_eligible_line_is_rejected() -> None:
    """整單都是贈品或不可折商品時，整單折扣無處可分攤——直接擋下比默默折 0 誠實。"""
    with pytest.raises(InvalidDiscount, match="沒有可折扣"):
        apply_discounts([_gift("g", "300")], [_order(CalculationMethod.FIXED_AMOUNT, "50")])


# ── 疊加 ────────────────────────────────────────────────────────────────────


def test_item_discount_applies_before_order_discount_allocation() -> None:
    """順序固定：先單品折扣，再以**折後餘額**為基礎分攤整單折扣。

    以餘額為基礎才不會讓已被打到很低的行分到超過它剩餘金額的整單折扣。
    """
    result = apply_discounts(
        [_normal("a", "600"), _normal("b", "400")],
        [
            _item("a", CalculationMethod.FIXED_AMOUNT, "200"),  # a → 400
            _order(CalculationMethod.FIXED_AMOUNT, "80"),  # 基礎 400+400=800 → 各 40
        ],
    )
    assert result.item_discount_amount == Decimal(200)
    assert result.order_discount_amount == Decimal(80)
    assert result.manual_discount_by_line == {"a": Decimal(240), "b": Decimal(40)}
    assert result.net_by_line == {"a": Decimal(360), "b": Decimal(360)}
    assert result.net_amount == Decimal(720)


def test_multiple_item_discounts_on_the_same_line_accumulate() -> None:
    result = apply_discounts(
        [_normal("a", "1000")],
        [
            _item("a", CalculationMethod.FIXED_AMOUNT, "100"),
            _item("a", CalculationMethod.PERCENTAGE, "10"),  # 對餘額 900 折 10% = 90
        ],
    )
    assert result.manual_discount_by_line == {"a": Decimal(190)}
    assert result.net_by_line == {"a": Decimal(810)}


# ── 輸入守衛 ────────────────────────────────────────────────────────────────


def test_discount_value_must_be_positive() -> None:
    with pytest.raises(InvalidDiscount, match="必須大於 0"):
        apply_discounts([_normal("a", "100")], [_item("a", CalculationMethod.FIXED_AMOUNT, "0")])
    with pytest.raises(InvalidDiscount, match="必須大於 0"):
        apply_discounts([_normal("a", "100")], [_order(CalculationMethod.PERCENTAGE, "-5")])


def test_percentage_may_not_reach_one_hundred() -> None:
    """100% ＝ 免費，一律走贈品（與 core/money.py 的活動折扣同一條線）。"""
    with pytest.raises(InvalidDiscount, match="百分比"):
        apply_discounts([_normal("a", "100")], [_item("a", CalculationMethod.PERCENTAGE, "100")])


def test_item_scope_requires_a_target_and_order_scope_forbids_one() -> None:
    with pytest.raises(InvalidDiscount, match="指定"):
        apply_discounts(
            [_normal("a", "100")],
            [DiscountRequest(AdjustmentScope.ITEM, CalculationMethod.FIXED_AMOUNT, Decimal(10))],
        )
    with pytest.raises(InvalidDiscount, match="整單"):
        apply_discounts(
            [_normal("a", "100")],
            [
                DiscountRequest(
                    AdjustmentScope.ORDER,
                    CalculationMethod.FIXED_AMOUNT,
                    Decimal(10),
                    target_key="a",
                )
            ],
        )


def test_gift_only_order_nets_to_zero_without_any_discount() -> None:
    """全贈品單：應付 0、贈品價值另計——這是店主裁示要支援的情形。"""
    result = apply_discounts([_gift("g1", "300"), _gift("g2", "200")], [])
    assert result.net_amount == Decimal(0)
    assert result.gift_retail_value == Decimal(500)
    assert result.gross_amount == Decimal(0)  # 牌價小計只算一般銷售


def test_result_does_not_depend_on_the_order_the_clerk_applied_the_discounts() -> None:
    """規格 §4：套用順序固定為「先單品、後整單」，不依呼叫端給的陣列順序。

    依陣列順序執行的話，店員先點整單再點單品 vs 反過來，同樣兩筆折扣會算出不同的應付
    金額（600/400 兩行，單品折 200 + 整單 10% → 720 或 700）。**客人付多少不該取決於
    店員的點擊順序**（Codex 第七輪）。
    """
    lines = [_normal("a", "600"), _normal("b", "400")]
    item = _item("a", CalculationMethod.FIXED_AMOUNT, "200")
    order = _order(CalculationMethod.PERCENTAGE, "10")

    item_first = apply_discounts(lines, [item, order])
    order_first = apply_discounts(lines, [order, item])

    assert item_first.net_amount == order_first.net_amount == Decimal(720)
    assert item_first.manual_discount_by_line == order_first.manual_discount_by_line
    # 且確實是「以單品折後餘額為基礎」算整單折扣（800 的 10% = 80，不是 1000 的 100）
    assert item_first.order_discount_amount == Decimal(80)
