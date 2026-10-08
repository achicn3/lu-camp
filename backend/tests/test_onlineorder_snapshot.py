"""線上菜單快照（docs/44 §3.5）：店內菜單 → 公開給客人看的 JSON。

快照會公開在網路上：**不可含成本**、不含停售／封存的品項；金額一律整數元。
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.modules.menu.models import MenuCategory, MenuItem, MenuOption, MenuOptionGroup
from app.modules.menu.service import MenuItemDetail, OptionGroupDetail
from app.modules.onlineorder.snapshot import build_snapshot, snapshot_text

TODAY = date(2026, 10, 2)


def _item(**kw: object) -> MenuItem:
    base: dict[str, object] = {
        "id": 5,
        "store_id": 1,
        "name": "拿鐵",
        "unit_price": Decimal(150),
        "unit_cost": Decimal(40),
        "description": "濃縮咖啡加鮮奶",
        "photo_sha256": "a" * 64,
        "is_available": True,
        "daily_limited": False,
        "stock_qty": None,
        "stock_day": None,
        "sort_order": 0,
        "archived_at": None,
    }
    base.update(kw)
    return MenuItem(**base)


def _option(oid: int, name: str, delta: int, **kw: object) -> MenuOption:
    base: dict[str, object] = {
        "id": oid,
        "store_id": 1,
        "group_id": 1,
        "name": name,
        "price_delta": Decimal(delta),
        "unit_cost": Decimal(8),
        "is_available": True,
        "daily_limited": False,
        "stock_qty": None,
        "stock_day": None,
        "sort_order": oid,
    }
    base.update(kw)
    return MenuOption(**base)


COFFEE = MenuCategory(id=1, store_id=1, name="咖啡", sort_order=0)
DESSERT = MenuCategory(id=2, store_id=1, name="甜點", sort_order=1)
EMPTY = MenuCategory(id=3, store_id=1, name="沒東西的分類", sort_order=2)
MILK = MenuOptionGroup(id=1, store_id=1, name="奶", min_select=1, max_select=1, sort_order=0)


def _detail(
    item: MenuItem, category: MenuCategory | None, groups: list[OptionGroupDetail]
) -> MenuItemDetail:
    return MenuItemDetail(item=item, category=category, option_groups=groups)


def _build(details: list[MenuItemDetail]) -> dict[str, Any]:
    return build_snapshot(
        details,
        [COFFEE, DESSERT, EMPTY],
        store_name="露坑",
        version=3,
        published_at=datetime(2026, 10, 2, 3, 0, tzinfo=UTC),
        font_sha256="f" * 64,
        day=TODAY,
    )


def test_snapshot_shape_and_money_as_whole_yuan() -> None:
    oat = _option(11, "燕麥奶", 20)
    snap = _build(
        [
            _detail(
                _item(category_id=1),
                COFFEE,
                [OptionGroupDetail(group=MILK, options=[_option(10, "鮮奶", 0), oat])],
            )
        ]
    )
    assert snap == {
        "version": 3,
        "published_at": "2026-10-02T03:00:00+00:00",
        "store_name": "露坑",
        "font": "f" * 64,
        "categories": [{"id": 1, "name": "咖啡"}],
        "items": [
            {
                "id": 5,
                "name": "拿鐵",
                "description": "濃縮咖啡加鮮奶",
                "category_id": 1,
                "unit_price": 150,
                "photo": "a" * 64,
                "available": True,
                "remaining": None,
                "option_groups": [
                    {
                        "id": 1,
                        "name": "奶",
                        "min_select": 1,
                        "max_select": 1,
                        "options": [
                            {
                                "id": 10,
                                "name": "鮮奶",
                                "price_delta": 0,
                                "available": True,
                                "remaining": None,
                            },
                            {
                                "id": 11,
                                "name": "燕麥奶",
                                "price_delta": 20,
                                "available": True,
                                "remaining": None,
                            },
                        ],
                    }
                ],
            }
        ],
        "experiences": [],
        "retail": [],
    }


def test_cost_never_leaks() -> None:
    snap = _build(
        [
            _detail(
                _item(category_id=1),
                COFFEE,
                [OptionGroupDetail(group=MILK, options=[_option(10, "鮮奶", 0)])],
            )
        ]
    )
    text = str(snap)
    assert "cost" not in text
    assert "40" not in text.replace("2026-10-02T03:00:00+00:00", "")


def test_unavailable_and_archived_items_are_left_out() -> None:
    snap = _build(
        [
            _detail(_item(id=1, name="可賣", category_id=1), COFFEE, []),
            _detail(_item(id=2, name="停售", is_available=False, category_id=1), COFFEE, []),
            _detail(
                _item(
                    id=3, name="封存", archived_at=datetime(2026, 1, 1, tzinfo=UTC), category_id=1
                ),
                COFFEE,
                [],
            ),
        ]
    )
    assert [i["name"] for i in snap["items"]] == ["可賣"]


def test_daily_limited_shows_remaining_and_sold_out() -> None:
    snap = _build(
        [
            _detail(
                _item(id=1, name="戚風", daily_limited=True, stock_qty=2, stock_day=TODAY),
                DESSERT,
                [],
            ),
            _detail(
                _item(
                    id=2, name="司康", daily_limited=True, stock_qty=5, stock_day=date(2026, 10, 1)
                ),
                DESSERT,
                [],
            ),
        ]
    )
    remaining = {i["name"]: i["remaining"] for i in snap["items"]}
    assert remaining == {"戚風": 2, "司康": 0}  # 昨天填的＝今天歸零


def test_unavailable_option_is_kept_but_marked() -> None:
    group = OptionGroupDetail(
        group=MILK, options=[_option(10, "鮮奶", 0), _option(11, "燕麥奶", 20, is_available=False)]
    )
    snap = _build([_detail(_item(category_id=1), COFFEE, [group])])
    options = snap["items"][0]["option_groups"][0]["options"]
    assert [(o["name"], o["available"]) for o in options] == [("鮮奶", True), ("燕麥奶", False)]


def test_only_categories_with_items_and_uncategorized_ok() -> None:
    snap = _build([_detail(_item(category_id=None), None, [])])
    assert snap["categories"] == []
    assert snap["items"][0]["category_id"] is None


def test_snapshot_text_covers_everything_customers_read() -> None:
    group = OptionGroupDetail(group=MILK, options=[_option(10, "鮮奶", 0)])
    snap = _build([_detail(_item(category_id=1), COFFEE, [group])])
    text = snapshot_text(snap)
    for word in ("露坑", "咖啡", "拿鐵", "濃縮咖啡加鮮奶", "奶", "鮮奶"):
        assert word in text


def test_snapshot_text_includes_presentation_copy() -> None:
    snapshot = _build([_detail(_item(category_id=1), COFFEE, [])])
    snapshot["items"][0]["presentation"] = {
        "flavor_description": "蜜桃花香",
        "audience_description": "喜歡清爽果香的你",
    }
    text = snapshot_text(snapshot)
    assert "蜜桃花香" in text
    assert "喜歡清爽果香的你" in text
