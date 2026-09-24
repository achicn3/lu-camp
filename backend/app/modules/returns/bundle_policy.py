"""組合價的整組退規則（docs/40 §8、裁示 5；純函式）。

一行可能只有部分件數在組內（例如 3 罐瓦斯只有 2 罐進組）。組外的件可以先退、不牽動組合；
一旦這次退貨會動到組內的件，那一組用到的**每一行都要把剩下的件全部退回**，同一行又屬於
別組的，那組也一起退（一路連下去）。

為什麼是「整行」而不只是組內件數：退款依一行的實付按件數平均（差額法），同一行組內與組外
的件實付不同。只退組內件數，留下來的那件等於用平均價（含組合折扣）買到——拿著折扣走人
（Codex 審查）。整行退回時平均就等於實付，一元不差。
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
    """這次退貨會整組退回哪些組合；動到組內的件卻沒把相關的行整行退回 → ReturnLineInvalid。"""
    bundled: dict[int, int] = {}
    for group in groups:
        for line_id, qty in group.members:
            bundled[line_id] = bundled.get(line_id, 0) + qty

    whole_lines = {
        line_id
        for line_id, qty in requested.items()
        if line_id in bundled
        and previous.get(line_id, 0) + qty > line_qty[line_id] - bundled[line_id]
    }
    returning: set[int] = set()
    changed = True
    while changed:  # 組 → 行 → 組…連下去，直到沒有新的
        changed = False
        for group in groups:
            if group.group_id in returning:
                continue
            if any(line_id in whole_lines for line_id, _ in group.members):
                returning.add(group.group_id)
                whole_lines.update(line_id for line_id, _ in group.members)
                changed = True

    if any(
        requested.get(line_id, 0) < line_qty[line_id] - previous.get(line_id, 0)
        for line_id in whole_lines
    ):
        raise ReturnLineInvalid(
            "組合價商品必須整組退：請把同一組的每一件都勾選退回"
            "（同一品項有多件時，剩下的件數也要一起退）"
        )
    return [g.group_id for g in groups if g.group_id in returning]
