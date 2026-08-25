"""毛利報表的純金額組合規則。"""

from decimal import Decimal

from app.modules.sales.service import _contribution_margin, _net_payment_methods
from app.shared.enums import TenderType


def test_net_payment_methods_subtract_refunds_and_preserve_original_fees() -> None:
    result = _net_payment_methods(
        (
            ("CASH", Decimal(100), Decimal(0)),
            ("LINE_PAY", Decimal(200), Decimal(4)),
        ),
        {
            TenderType.CASH: Decimal(150),
            TenderType.STORE_CREDIT: Decimal(50),
        },
    )
    assert result == (
        ("CASH", Decimal(-50), Decimal(0)),
        ("STORE_CREDIT", Decimal(-50), Decimal(0)),
        ("LINE_PAY", Decimal(200), Decimal(4)),
    )


def test_contribution_margin_reverses_returned_gift_cost() -> None:
    assert _contribution_margin(
        gross_margin=Decimal(1000),
        payment_fee_total=Decimal(30),
        gift_cost=Decimal(200),
        gift_returned_cost=Decimal(80),
    ) == Decimal(850)
