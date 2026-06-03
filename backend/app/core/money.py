"""金額工具：新台幣整數元（無角分），一律用 Decimal、ROUND_HALF_UP。

本檔目前提供 T5 所需的 round_ntd 與定價輔助 suggested_price；
split_tax_inclusive / commission 等於對應 phase（發票/寄售）導入時再加入。
"""

from decimal import ROUND_HALF_UP, Decimal

from app.shared.exceptions import InvalidMargin

MARGIN_MIN = 0
MARGIN_MAX = 99


def round_ntd(value: Decimal) -> int:
    """四捨五入（ROUND_HALF_UP）到整數元。"""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def suggested_price(acquisition_cost: Decimal, margin_pct: int) -> int:
    """建議含稅售價 = round_ntd(收購價 / (1 - margin_pct/100))。

    margin_pct 為整數百分數，限 0-99；>=100 或 <0 會除以零/負值，視為錯誤。
    """
    if not MARGIN_MIN <= margin_pct <= MARGIN_MAX:
        raise InvalidMargin(f"margin_pct 須介於 {MARGIN_MIN}-{MARGIN_MAX}，收到 {margin_pct}")
    divisor = Decimal(1) - Decimal(margin_pct) / Decimal(100)
    return round_ntd(acquisition_cost / divisor)
