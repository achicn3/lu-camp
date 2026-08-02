"""退款金額的差額法（純函式，無 DB）。

分次退貨最容易出的錯是「每次各自四捨五入」：加總與原實付差幾元，少退坑客人、多退店家虧，
而且永遠對不平。這裡每條規則都能被單獨證偽。
"""

from decimal import Decimal

import pytest

from app.modules.returns.refund import line_refund_amount, refund_entitlement


def test_returning_everything_at_once_refunds_exactly_what_was_paid() -> None:
    assert line_refund_amount(Decimal(540), 3, 0, 3) == Decimal(540)


def test_partial_returns_sum_to_the_original_amount() -> None:
    """540 ÷ 3 除不盡的情形：三次分退的加總必須恰好等於 540。"""
    net, qty = Decimal(500), 3
    first = line_refund_amount(net, qty, 0, 1)
    second = line_refund_amount(net, qty, 1, 1)
    third = line_refund_amount(net, qty, 2, 1)
    assert first + second + third == net
    # 每一步都是「累計應退」的差額：167(=500/3四捨五入) → 333 → 500，故差額為 167/166/167。
    # 保證的是**加總恰好等於原實付**，不是每次都拿到一樣的數字。
    assert (first, second, third) == (Decimal(167), Decimal(166), Decimal(167))


def test_each_partial_step_never_overshoots_the_original_amount() -> None:
    net, qty = Decimal(1000), 7
    total = Decimal(0)
    for already in range(qty):
        total += line_refund_amount(net, qty, already, 1)
        assert total <= net
    assert total == net


def test_returning_several_at_once_equals_returning_them_one_by_one() -> None:
    """一次退 2 件 == 分兩次各退 1 件，否則店員的操作順序會改變退款金額。"""
    net, qty = Decimal(500), 3
    at_once = line_refund_amount(net, qty, 0, 2)
    one_by_one = line_refund_amount(net, qty, 0, 1) + line_refund_amount(net, qty, 1, 1)
    assert at_once == one_by_one


def test_a_gift_line_refunds_nothing() -> None:
    """贈品要退回庫存，但沒有錢可退。"""
    assert line_refund_amount(Decimal(0), 2, 0, 1) == Decimal(0)
    assert line_refund_amount(Decimal(0), 2, 1, 1) == Decimal(0)


def test_entitlement_is_zero_before_anything_is_returned() -> None:
    assert refund_entitlement(Decimal(500), 3, 0) == Decimal(0)


def test_out_of_range_quantities_are_rejected() -> None:
    with pytest.raises(ValueError, match="介於"):
        refund_entitlement(Decimal(500), 3, 4)
    with pytest.raises(ValueError, match="介於"):
        refund_entitlement(Decimal(500), 3, -1)
    with pytest.raises(ValueError, match="必須大於 0"):
        line_refund_amount(Decimal(500), 3, 0, 0)
    with pytest.raises(ValueError, match="必須大於 0"):
        refund_entitlement(Decimal(500), 0, 0)
