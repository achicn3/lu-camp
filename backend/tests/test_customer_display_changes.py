"""客顯購物車視覺事件的純函式回歸測試。"""

from app.modules.customerdisplay.service import _cart_changes


def test_discount_recalculation_is_an_explicit_customer_display_change() -> None:
    item: dict[str, object] = {
        "item_key": "CATALOG:1",
        "name": "露營燈",
        "qty": 1,
        "unit_price": "900",
    }
    old: dict[str, object] = {"items": [item], "discount_total": "0"}
    new: dict[str, object] = {"items": [item], "discount_total": "100"}

    assert _cart_changes(old, new) == [
        {
            "type": "DISCOUNT_CHANGED",
            "item_key": "TOTAL",
            "name": "折扣已重新計算",
            "from_qty": None,
            "to_qty": None,
        }
    ]
