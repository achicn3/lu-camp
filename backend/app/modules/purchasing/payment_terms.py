"""採購單付款期限（docs/99 §5.2）：純函式，無 I/O。

付款條件在下單（送出）當下從供應商快照到採購單；之後供應商改條件不改寫舊單的期限，
跟供應商名快照同一個道理。期限由快照與日期即時算出，管理者手動改過的另外存。
"""

import calendar
from datetime import date, timedelta

from app.shared.enums import SupplierPaymentTerms
from app.shared.exceptions import InvalidPurchaseOrder

MONTHLY_DAY_MIN = 1
MONTHLY_DAY_MAX = 31
NET_DAYS_MAX = 365


def validate_terms(terms: SupplierPaymentTerms | None, value: int | None) -> None:
    """月結要填幾號（1–31）、到貨後要填幾天（0–365）；其他條件不填數字。"""
    if terms is SupplierPaymentTerms.MONTHLY:
        if value is None or not MONTHLY_DAY_MIN <= value <= MONTHLY_DAY_MAX:
            raise InvalidPurchaseOrder("月結要填每月幾號付（1 到 31）")
    elif terms is SupplierPaymentTerms.NET_DAYS:
        if value is None or not 0 <= value <= NET_DAYS_MAX:
            raise InvalidPurchaseOrder(f"到貨後幾天付要填 0 到 {NET_DAYS_MAX}")
    elif value is not None:
        raise InvalidPurchaseOrder("這種付款條件不用填數字")


def _next_month_day(base: date, day: int) -> date:
    year, month = (base.year + 1, 1) if base.month == 12 else (base.year, base.month + 1)
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def payment_due_on(
    terms: SupplierPaymentTerms | None,
    value: int | None,
    *,
    ordered_on: date | None,
    expected_on: date | None,
    first_received_on: date | None,
) -> date | None:
    """依付款條件算出付款期限；資料還不夠（例如月結但還沒到貨）回 None。"""
    if terms is SupplierPaymentTerms.PREPAID:
        return ordered_on
    if terms is SupplierPaymentTerms.COD:
        return first_received_on or expected_on
    if first_received_on is None or value is None:
        return None
    if terms is SupplierPaymentTerms.MONTHLY:
        return _next_month_day(first_received_on, value)
    if terms is SupplierPaymentTerms.NET_DAYS:
        return first_received_on + timedelta(days=value)
    return None
