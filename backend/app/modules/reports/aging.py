"""購物金負債帳齡分桶（docs/16 §5A）：未兌付餘額按**發出時間**分桶，扣抵以 FIFO 沖銷發出列。

純函數、無 DB 依賴：輸入各會員的「發出列（正向 entry：金額＋發出時間）」與「已消耗總額」
（＝Σ 正向 − 目前餘額），FIFO 由最舊發出列開始沖銷，剩餘者按帳齡落桶。報表推導用，不入帳本。
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

# 桶邊界（天）：<30 / 30–90 / 90–180 / 180–365 / >365。
_BUCKET_EDGES_DAYS = (30, 90, 180, 365)
BUCKET_KEYS = ("lt_30d", "d30_90", "d90_180", "d180_365", "gt_365d")


@dataclass(frozen=True)
class IssuedLot:
    """一筆發出（正向）列：金額（整數元、>0）與發出時間。"""

    amount: Decimal
    issued_at: datetime


def _bucket_for_age(age_days: float) -> str:
    for edge, key in zip(_BUCKET_EDGES_DAYS, BUCKET_KEYS, strict=False):
        if age_days < edge:
            return key
    return BUCKET_KEYS[-1]


def age_outstanding(
    lots: list[IssuedLot], consumed_total: Decimal, now: datetime
) -> "OrderedDict[str, Decimal]":
    """FIFO 沖銷後，把各發出列的剩餘額按帳齡落桶；回各桶金額（Σ = 未兌付餘額）。

    lots 不需預先排序（本函式依 issued_at 由舊到新排序）。consumed_total 為非負已消耗額；
    超出全部發出額時夾住（不產生負桶）。
    """
    buckets: OrderedDict[str, Decimal] = OrderedDict((k, Decimal(0)) for k in BUCKET_KEYS)
    remaining_to_consume = consumed_total if consumed_total > 0 else Decimal(0)
    for lot in sorted(lots, key=lambda lot_: lot_.issued_at):
        lot_remaining = lot.amount
        if remaining_to_consume > 0:
            take = min(lot_remaining, remaining_to_consume)
            lot_remaining -= take
            remaining_to_consume -= take
        if lot_remaining <= 0:
            continue
        age_days = (now - lot.issued_at).total_seconds() / 86400
        buckets[_bucket_for_age(age_days)] += lot_remaining
    return buckets
