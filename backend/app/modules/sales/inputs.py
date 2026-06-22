"""sales 領域層輸入 DTO（非 API schema）。

T11 為領域層，尚無對外 API；service 直接收這些輸入。對外 Pydantic schema 與路由屬 T12。
"""

from dataclasses import dataclass
from decimal import Decimal

from app.shared.enums import SaleLineType, TenderType


@dataclass(frozen=True)
class SaleLineInput:
    """一筆銷售明細輸入。依 line_type 擇一帶入參照：

    SERIALIZED → item_code（qty 固定 1）；CATALOG → catalog_product_id + qty；
    BULK_LOT → bulk_lot_id + qty；MENU → menu_item_id + qty（餐飲，不扣庫存）。
    """

    line_type: SaleLineType
    item_code: str | None = None
    catalog_product_id: int | None = None
    bulk_lot_id: int | None = None
    menu_item_id: int | None = None
    qty: int = 1


@dataclass(frozen=True)
class TenderInput:
    """一筆收款明細輸入（SC-3）：型別＋金額（整數元、>0）。

    省略整份 tenders 時，service 預設單一 CASH 全額（向後相容）。
    """

    tender_type: TenderType
    amount: Decimal
