"""組合價的整組退規則（docs/40 §8、裁示 5；純函式）。

一行可能只有部分件數在組內（例如 3 罐瓦斯只有 2 罐進組）。只要這次退貨動到組合用到的任何一行，
那一組用到的**每一行都要把剩下的件全部退回**，同一行又屬於別組的，那組也一起退（一路連下去）。

為什麼是「整組整行」：退款依一行的實付按件數平均（差額法），同一行組內與組外的件實付不同。
只退組內件數，留下來的那件等於用含組合折扣的平均價買到（店家吃虧）；先退組外那件，又只退到
平均價（客人吃虧）——兩個方向都不準（Codex 審查）。整行退回時平均就等於實付，一元不差。
要分開退，得先把組內／組外的實付分開記，那是另一個工程。
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
    bundled_lines = {line_id for group in groups for line_id, _ in group.members}
    whole_lines = {
        line_id for line_id, qty in requested.items() if qty > 0 and line_id in bundled_lines
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
