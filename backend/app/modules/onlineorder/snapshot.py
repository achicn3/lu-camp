"""線上菜單快照（docs/44 §3.5）：店內菜單 → 公開給客人看的 JSON。

這份 JSON 會公開在網路上：**只放客人該看的欄位，不放成本**；停售、封存的品項不列。
金額一律整數元（int）。售完／剩幾份是發佈當下的數字，即時更新在 O4 處理。
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from app.modules.menu.models import MenuCategory
from app.modules.menu.service import MenuItemDetail, remaining_today

# 寫成 Any：形狀由 build_snapshot 決定、由雲端與測試把關，這裡只是 JSON 容器。
Snapshot = dict[str, Any]


def _option_groups(detail: MenuItemDetail, day: date) -> list[dict[str, Any]]:
    return [
        {
            "id": g.group.id,
            "name": g.group.name,
            "min_select": g.group.min_select,
            "max_select": g.group.max_select,
            "options": [
                {
                    "id": o.id,
                    "name": o.name,
                    "price_delta": int(o.price_delta),
                    "available": o.is_available,
                    "remaining": remaining_today(o, day),
                }
                for o in g.options
            ],
        }
        for g in detail.option_groups
    ]


def build_snapshot(
    details: Sequence[MenuItemDetail],
    categories: Sequence[MenuCategory],
    *,
    store_name: str,
    version: int,
    published_at: datetime,
    font_sha256: str | None,
    day: date,
    presentations: Mapping[int, dict[str, Any]] | None = None,
) -> Snapshot:
    """組快照。品項依傳入順序（菜單排序）；分類只列有品項的，依分類排序。"""
    shown = [d for d in details if d.item.archived_at is None and d.item.is_available]
    used = {d.item.category_id for d in shown}
    return {
        "version": version,
        "published_at": published_at.isoformat(),
        "store_name": store_name,
        "font": font_sha256,
        "categories": [
            {"id": c.id, "name": c.name}
            for c in sorted(categories, key=lambda c: (c.sort_order, c.id))
            if c.id in used
        ],
        "items": [
            {
                "id": d.item.id,
                "name": d.item.name,
                "description": d.item.description,
                "category_id": d.item.category_id,
                "unit_price": int(d.item.unit_price),
                "photo": d.item.photo_sha256,
                "available": True,
                "remaining": remaining_today(d.item, day),
                "option_groups": _option_groups(d, day),
                **(
                    {"presentation": presentations[d.item.id]}
                    if presentations is not None and d.item.id in presentations
                    else {}
                ),
            }
            for d in shown
        ],
    }


def snapshot_text(snapshot: Snapshot) -> str:
    """客人會在點餐頁讀到的所有文字（給手寫字型子集用）。"""
    parts: list[str] = [str(snapshot["store_name"])]
    parts += [str(c["name"]) for c in snapshot["categories"]]
    for item in snapshot["items"]:
        parts += [str(item["name"]), str(item["description"] or "")]
        presentation = item.get("presentation")
        if presentation is not None:
            parts += [
                str(presentation.get("flavor_description") or ""),
                str(presentation.get("audience_description") or ""),
            ]
        for group in item["option_groups"]:
            parts.append(str(group["name"]))
            parts += [str(o["name"]) for o in group["options"]]
    return "".join(parts)
