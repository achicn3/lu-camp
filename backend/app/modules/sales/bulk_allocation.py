"""販售籃行的來源分配規則（ADR-025），純函式。

退貨依「後分配的先回」反向回補：同一個累計退貨量，永遠對應同一組各來源回補量。
退貨流程據此決定庫存回到哪個來源，報表據此反轉成本——兩邊同一個規則，
帳上成本才會等於回到各來源的庫存價值。
"""

from collections.abc import Sequence
from decimal import Decimal

from app.core.money import round_ntd


def returned_split(qtys: Sequence[int], returned_total: int) -> list[int]:
    """累計退回 returned_total 件時，各分配（依分配順序）各退了幾件。"""
    if returned_total < 0 or returned_total > sum(qtys):
        raise ValueError(f"累計退貨量 {returned_total} 超出可退範圍 0–{sum(qtys)}")
    split = [0] * len(qtys)
    remaining = returned_total
    for index in range(len(qtys) - 1, -1, -1):
        take = min(qtys[index], remaining)
        split[index] = take
        remaining -= take
    return split


def returned_cost(allocations: Sequence[tuple[int, Decimal]], returned_total: int) -> int:
    """累計退回 returned_total 件對應的成本：各分配按比例、整數元 HALF_UP 後加總。

    allocations＝[(件數, 成本快照)…]，依分配順序。
    """
    split = returned_split([qty for qty, _ in allocations], returned_total)
    return sum(
        round_ntd(cost * Decimal(back) / Decimal(qty))
        for (qty, cost), back in zip(allocations, split, strict=True)
    )
