"""core/money.py — NT$ 整數元四捨五入與定價輔助。"""

import inspect
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

# 營業稅率：測試用的固定值（正式環境一律取自 settings，§6 不得寫死）。
RATE = Decimal("0.05")


def test_round_ntd_half_up() -> None:
    assert round_ntd(Decimal("100.5")) == 101
    assert round_ntd(Decimal("100.4")) == 100
    assert round_ntd(Decimal("0.5")) == 1
    assert round_ntd(Decimal("2.5")) == 3


def test_suggested_price_margin_zero_equals_cost() -> None:
    assert suggested_price(Decimal("1000"), 0, RATE) == 1050


def test_suggested_price_margin_99() -> None:
    # 1000 / (1 - 0.99) = 1000 / 0.01 = 100000
    assert suggested_price(Decimal("1000"), 99, RATE) == 105000


def test_suggested_price_typical_rounds_to_integer_ntd() -> None:
    # 未稅 600/0.55 = 1090.909…；×1.05 = 1145.4545… → ROUND_HALF_UP → 1145
    assert suggested_price(Decimal("600"), 45, RATE) == 1145


def test_suggested_price_precision_at_non_five_percent_rates() -> None:
    """非 5% 稅率不得因中途截斷而少一元。

    先除後乘會讓 Decimal 在 28 位有效位數處截斷：cost 282／margin 1／稅率 7.25%
    得 305，但精確值是 305.5 → 應為 306。5% 下看不出來，而稅率是可設定的。
    """
    assert suggested_price(Decimal("282"), 1, Decimal("0.0725")) == 306


def test_suggested_price_requires_explicit_tax_rate() -> None:
    """`tax_rate` 必須是必填、**沒有預設值**（ADR-016 決策 3）。

    給預設值等於把 5% 藏進程式碼（違反 §6「稅率不得寫死」），而且設定讀取失敗時
    會靜默用錯的稅率算價，比明確報錯更危險。
    """
    sig = inspect.signature(suggested_price)
    assert sig.parameters["tax_rate"].default is inspect.Parameter.empty


def test_suggested_price_tax_rate_zero_falls_back_to_legacy_formula() -> None:
    """稅率 0 時必須回到 2026-08-23 前的舊式。

    這條的用處是：日後有人把 tax_rate 誤傳成 0，測試會證明那是「沒加稅」，
    而不是新公式壞了。
    """
    assert suggested_price(Decimal("600"), 45, Decimal(0)) == 1091
    assert suggested_price(Decimal("1000"), 45, Decimal(0)) == 1818


def test_suggested_price_rounds_once_not_twice() -> None:
    """釘住「只四捨五入一次」——挑的必須是兩種實作會分家的輸入，否則這條測試沒有鑑別力。

    600/45%：未稅 1090.909…
      單次取整：1090.909… × 1.05 = 1145.4545… → **1145**
      兩段式  ：先取整未稅 1091 → × 1.05 = 1145.55 → **1146**
    """
    assert suggested_price(Decimal("600"), 45, RATE) == 1145
    # 對照組：1000/45% 在兩種實作下同為 1909，單看它分不出來，故不能只靠這一條。
    assert suggested_price(Decimal("1000"), 45, RATE) == 1909


@pytest.mark.parametrize("rate", [Decimal("-0.01"), Decimal("1"), Decimal("1.5")])
def test_suggested_price_invalid_tax_rate_raises(rate: Decimal) -> None:
    with pytest.raises(InvalidTaxRate):
        suggested_price(Decimal("1000"), 45, rate)


@pytest.mark.parametrize("margin", [100, 150, -1])
def test_suggested_price_invalid_margin_raises(margin: int) -> None:
    with pytest.raises(InvalidMargin):
        suggested_price(Decimal("1000"), margin, RATE)


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
