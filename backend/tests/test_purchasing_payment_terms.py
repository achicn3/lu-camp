"""採購單付款期限（docs/99 §5.2）：依下單時快照的供應商付款條件，從日期算出最晚哪天要付。

- 先付：實際下單日
- 貨到付款：第一批到貨日；還沒到貨時用預計到貨日
- 月結（每月 X 號付上個月的）：第一批到貨日所在月份的下個月 X 號（小月落月底）；還沒到貨空白
- 到貨後 N 天：第一批到貨日 + N 天；還沒到貨空白
"""

from datetime import date

import pytest

from app.modules.purchasing.payment_terms import payment_due_on, validate_terms
from app.shared.enums import SupplierPaymentTerms
from app.shared.exceptions import InvalidPurchaseOrder

ORDERED = date(2026, 10, 20)
EXPECTED = date(2026, 10, 28)
RECEIVED = date(2026, 10, 29)


def test_no_terms_means_no_due_date() -> None:
    assert (
        payment_due_on(None, None, ordered_on=ORDERED, expected_on=EXPECTED, first_received_on=None)
        is None
    )


def test_prepaid_is_due_on_the_order_day() -> None:
    assert (
        payment_due_on(
            SupplierPaymentTerms.PREPAID,
            None,
            ordered_on=ORDERED,
            expected_on=EXPECTED,
            first_received_on=None,
        )
        == ORDERED
    )


def test_cash_on_delivery_follows_arrival_then_expected() -> None:
    cod = SupplierPaymentTerms.COD
    assert (
        payment_due_on(cod, None, ordered_on=ORDERED, expected_on=EXPECTED, first_received_on=None)
        == EXPECTED
    )
    assert (
        payment_due_on(
            cod, None, ordered_on=ORDERED, expected_on=EXPECTED, first_received_on=RECEIVED
        )
        == RECEIVED
    )
    assert (
        payment_due_on(cod, None, ordered_on=ORDERED, expected_on=None, first_received_on=None)
        is None
    )


def test_monthly_is_next_month_with_month_end_fallback() -> None:
    monthly = SupplierPaymentTerms.MONTHLY
    assert payment_due_on(
        monthly, 10, ordered_on=ORDERED, expected_on=EXPECTED, first_received_on=RECEIVED
    ) == date(2026, 11, 10)
    # 一月收貨、每月 31 號付 → 二月沒有 31 號，落月底
    assert payment_due_on(
        monthly, 31, ordered_on=ORDERED, expected_on=None, first_received_on=date(2027, 1, 5)
    ) == date(2027, 2, 28)
    # 十二月收貨 → 隔年一月
    assert payment_due_on(
        monthly, 5, ordered_on=ORDERED, expected_on=None, first_received_on=date(2026, 12, 20)
    ) == date(2027, 1, 5)
    # 還沒到貨：月結不知道算哪個月，先空白
    assert (
        payment_due_on(
            monthly, 10, ordered_on=ORDERED, expected_on=EXPECTED, first_received_on=None
        )
        is None
    )


def test_net_days_counts_from_first_arrival() -> None:
    net = SupplierPaymentTerms.NET_DAYS
    assert payment_due_on(
        net, 30, ordered_on=ORDERED, expected_on=EXPECTED, first_received_on=RECEIVED
    ) == date(2026, 11, 28)
    assert (
        payment_due_on(net, 0, ordered_on=ORDERED, expected_on=EXPECTED, first_received_on=RECEIVED)
        == RECEIVED
    )
    assert (
        payment_due_on(net, 30, ordered_on=ORDERED, expected_on=EXPECTED, first_received_on=None)
        is None
    )


def test_prepaid_without_order_day_is_unknown() -> None:
    """草稿還沒有實際下單日：先付也還算不出來。"""
    assert (
        payment_due_on(
            SupplierPaymentTerms.PREPAID,
            None,
            ordered_on=None,
            expected_on=None,
            first_received_on=None,
        )
        is None
    )


@pytest.mark.parametrize(
    ("terms", "value"),
    [
        (None, None),
        (SupplierPaymentTerms.PREPAID, None),
        (SupplierPaymentTerms.COD, None),
        (SupplierPaymentTerms.MONTHLY, 1),
        (SupplierPaymentTerms.MONTHLY, 31),
        (SupplierPaymentTerms.NET_DAYS, 0),
        (SupplierPaymentTerms.NET_DAYS, 365),
    ],
)
def test_valid_terms(terms: SupplierPaymentTerms | None, value: int | None) -> None:
    validate_terms(terms, value)


@pytest.mark.parametrize(
    ("terms", "value"),
    [
        (None, 5),  # 沒有條件卻填了數字
        (SupplierPaymentTerms.PREPAID, 5),
        (SupplierPaymentTerms.COD, 1),
        (SupplierPaymentTerms.MONTHLY, None),
        (SupplierPaymentTerms.MONTHLY, 0),
        (SupplierPaymentTerms.MONTHLY, 32),
        (SupplierPaymentTerms.NET_DAYS, None),
        (SupplierPaymentTerms.NET_DAYS, -1),
        (SupplierPaymentTerms.NET_DAYS, 366),
    ],
)
def test_invalid_terms(terms: SupplierPaymentTerms | None, value: int | None) -> None:
    with pytest.raises(InvalidPurchaseOrder):
        validate_terms(terms, value)
