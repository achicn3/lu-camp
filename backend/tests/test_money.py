"""core/money.py — NT$ 整數元四捨五入與定價輔助。"""

from decimal import Decimal

import pytest

from app.core.money import round_ntd, suggested_price
from app.shared.exceptions import InvalidMargin


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
