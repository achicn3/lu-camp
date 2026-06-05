"""金額工具：新台幣整數元（無角分），一律用 Decimal、ROUND_HALF_UP。

本檔提供 round_ntd、定價輔助 suggested_price、發票稅額拆分 split_tax_inclusive
與寄售抽成 commission。
"""

from decimal import ROUND_HALF_UP, Decimal

from app.shared.exceptions import InvalidCommissionPct, InvalidMargin, InvalidTaxRate

MARGIN_MIN = 0
MARGIN_MAX = 99
COMMISSION_PCT_MIN = 0
COMMISSION_PCT_MAX = 100


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


def split_tax_inclusive(total: Decimal, rate: Decimal) -> tuple[int, int]:
    """將含稅總額拆為（未稅 net, 稅額 tax），保證 net + tax = total（整數元、不差一元）。

    稅於發票總額層級推算一次（不逐項算稅，見 CLAUDE.md §6）：
    `net = round_ntd(total / (1 + rate))`、`tax = total − net`。
    rate 為小數稅率（如 0.05），限 0 ≤ rate < 1。
    """
    if not Decimal(0) <= rate < Decimal(1):
        raise InvalidTaxRate(f"稅率須介於 0（含）至 1（不含），收到 {rate}")
    total_ntd = round_ntd(total)
    net = round_ntd(total / (Decimal(1) + rate))
    tax = total_ntd - net
    return net, tax


def commission(gross: Decimal, pct: int) -> int:
    """寄售抽成金額 = round_ntd(售價 × pct / 100)（§7.2）。

    pct 為整數百分數，限 0–100；超出視為錯誤（避免負抽成或 >全額）。
    應付寄售人 = gross − commission(gross, pct)，由呼叫端相減。
    """
    if not COMMISSION_PCT_MIN <= pct <= COMMISSION_PCT_MAX:
        raise InvalidCommissionPct(
            f"commission_pct 須介於 {COMMISSION_PCT_MIN}-{COMMISSION_PCT_MAX}，收到 {pct}"
        )
    return round_ntd(gross * Decimal(pct) / Decimal(100))
