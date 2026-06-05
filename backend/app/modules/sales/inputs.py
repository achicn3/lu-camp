"""sales 領域層輸入 DTO（非 API schema）。

T11 為領域層，尚無對外 API；service 直接收這些輸入。對外 Pydantic schema 與路由屬 T12。
"""

from dataclasses import dataclass

from app.shared.enums import SaleLineType


@dataclass(frozen=True)
class SaleLineInput:
    """一筆銷售明細輸入。依 line_type 擇一帶入參照：

    SERIALIZED → item_code（qty 固定 1）；CATALOG → catalog_product_id + qty；
    BULK_LOT → bulk_lot_id + qty。
    """

    line_type: SaleLineType
    item_code: str | None = None
    catalog_product_id: int | None = None
    bulk_lot_id: int | None = None
    qty: int = 1
