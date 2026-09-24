"""組合價的整組退規則（docs/40 §8、裁示 5；純函式）。

一行可能只有部分件數在組內（例如 3 罐瓦斯只有 2 罐進組）。組外的件先退、不牽動組合；
一旦這次退貨會動到組內的件，那一組的**每一行**都必須在同一張退貨單裡、退足組內件數，
否則拒絕——不能退掉帳篷、留著用組合價買到的椅子。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.shared.exceptions import ReturnLineInvalid


@dataclass(frozen=True)
class BundleGroupMembers:
    """一組尚未退回的組合價：(銷售明細 id, 組內件數)。"""

    group_id: int
    members: tuple[tuple[int, int], ...]


def bundles_to_return(
    requested: Mapping[int, int],
    previous: Mapping[int, int],
    line_qty: Mapping[int, int],
    groups: Sequence[BundleGroupMembers],
) -> list[int]:
    """這次退貨會整組退回哪些組合；動到組內的件卻沒退齊整組 → ReturnLineInvalid。"""
    bundled: dict[int, int] = {}
    for group in groups:
        for line_id, qty in group.members:
            bundled[line_id] = bundled.get(line_id, 0) + qty

    touched = {
        line_id
        for line_id, qty in requested.items()
        if line_id in bundled
        and previous.get(line_id, 0) + qty > line_qty[line_id] - bundled[line_id]
    }
    returning = [g for g in groups if any(line_id in touched for line_id, _ in g.members)]

    needed: dict[int, int] = {}
    for group in returning:
        for line_id, qty in group.members:
            needed[line_id] = needed.get(line_id, 0) + qty
    if any(requested.get(line_id, 0) < qty for line_id, qty in needed.items()):
        raise ReturnLineInvalid("組合價商品必須整組退：請把同一組的每一件都勾選退回")
    return [g.group_id for g in returning]
