"""core/money.py — NT$ 整數元四捨五入與定價輔助。"""

from decimal import Decimal

import pytest

from app.core.money import (
    commission,
    discounted_price,
    round_ntd,
    split_tax_inclusive,
    suggested_price,
)
from app.shared.exceptions import (
    InvalidCommissionPct,
    InvalidDiscountPct,
    InvalidMargin,
    InvalidTaxRate,
)


def test_round_ntd_half_up() -> None:
    assert round_ntd(Decimal("100.5")) == 101
    assert round_ntd(Decimal("100.4")) == 100
    assert round_ntd(Decimal("0.5")) == 1
    assert round_ntd(Decimal("2.5")) == 3


def test_suggested_price_margin_zero_equals_cost() -> None:
    assert suggested_price(Decimal("1000"), 0) == 1000


def test_suggested_price_margin_99() -> None:
    # 1000 / (1 - 0.99) = 1000 / 0.01 = 100000
    assert suggested_price(Decimal("1000"), 99) == 100000


def test_suggested_price_typical_rounds_to_integer_ntd() -> None:
    # 600 / 0.55 = 1090.909… → ROUND_HALF_UP → 1091
    assert suggested_price(Decimal("600"), 45) == 1091


@pytest.mark.parametrize("margin", [100, 150, -1])
def test_suggested_price_invalid_margin_raises(margin: int) -> None:
    with pytest.raises(InvalidMargin):
        suggested_price(Decimal("1000"), margin)


def test_split_tax_inclusive_exact() -> None:
    # 105 含稅、稅率 5% → net 100、tax 5
    net, tax = split_tax_inclusive(Decimal("105"), Decimal("0.05"))
    assert (net, tax) == (100, 5)


def test_split_tax_inclusive_invariant_net_plus_tax_equals_total() -> None:
    # 100 / 1.05 = 95.238… → net 95、tax = 100 - 95 = 5（保證不差一元）
    net, tax = split_tax_inclusive(Decimal("100"), Decimal("0.05"))
    assert net == 95
    assert tax == 5
    assert net + tax == 100


@pytest.mark.parametrize("total", [Decimal("0"), Decimal("1"), Decimal("33"), Decimal("99999")])
def test_split_tax_inclusive_always_sums_to_total(total: Decimal) -> None:
    net, tax = split_tax_inclusive(total, Decimal("0.05"))
    assert net + tax == int(total)
    assert net >= 0
    assert tax >= 0


def test_split_tax_inclusive_zero_rate_no_tax() -> None:
    net, tax = split_tax_inclusive(Decimal("100"), Decimal("0"))
    assert (net, tax) == (100, 0)


def test_split_tax_inclusive_rounds_total_before_splitting() -> None:
    # 含稅總額先 round_ntd 到整數元（100.6 → 101），稅再由整數總額推算：
    # net = round_ntd(100.6 / 1.05) = round_ntd(95.81) = 96、tax = 101 - 96 = 5
    net, tax = split_tax_inclusive(Decimal("100.6"), Decimal("0.05"))
    assert net == 96
    assert tax == 5
    assert net + tax == 101


@pytest.mark.parametrize("rate", [Decimal("-0.01"), Decimal("1"), Decimal("1.5")])
def test_split_tax_inclusive_invalid_rate_raises(rate: Decimal) -> None:
    with pytest.raises(InvalidTaxRate):
        split_tax_inclusive(Decimal("100"), rate)


def test_commission_default_50() -> None:
    # 售價 3000、抽成 50% → 1500；應付寄售人 = 3000 - 1500 = 1500
    assert commission(Decimal("3000"), 50) == 1500


def test_commission_rounds_half_up() -> None:
    # 999 × 50 / 100 = 499.5 → ROUND_HALF_UP → 500
    assert commission(Decimal("999"), 50) == 500


@pytest.mark.parametrize(
    ("gross", "pct", "expected"),
    [(Decimal("1000"), 0, 0), (Decimal("1000"), 100, 1000), (Decimal("1234"), 30, 370)],
)
def test_commission_bounds(gross: Decimal, pct: int, expected: int) -> None:
    assert commission(gross, pct) == expected


@pytest.mark.parametrize("pct", [-1, 101, 150])
def test_commission_invalid_pct_raises(pct: int) -> None:
    with pytest.raises(InvalidCommissionPct):
        commission(Decimal("1000"), pct)


def test_discounted_price_nine_tenths() -> None:
    # 九折（10% off）：1000 × 90% = 900
    assert discounted_price(Decimal("1000"), 10) == 900


def test_discounted_price_rounds_half_up() -> None:
    # 999 × 95% = 949.05 → 949；333 × 85% = 283.05 → 283
    assert discounted_price(Decimal("999"), 5) == 949
    # 1 × 50% = 0.5 → ROUND_HALF_UP → 1（折後不為 0）
    assert discounted_price(Decimal("1"), 50) == 1


@pytest.mark.parametrize(
    ("price", "pct", "expected"),
    [
        (Decimal("1000"), 1, 990),
        (Decimal("1000"), 99, 10),
        (Decimal("0"), 50, 0),
        (Decimal("250"), 20, 200),
    ],
)
def test_discounted_price_bounds(price: Decimal, pct: int, expected: int) -> None:
    result = discounted_price(price, pct)
    assert result == expected
    assert 0 <= result <= price  # 折後介於 0 與原價之間


@pytest.mark.parametrize("pct", [0, 100, -1, 150])
def test_discounted_price_invalid_pct_raises(pct: int) -> None:
    with pytest.raises(InvalidDiscountPct):
        discounted_price(Decimal("1000"), pct)
