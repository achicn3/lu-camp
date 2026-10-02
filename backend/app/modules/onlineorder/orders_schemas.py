"""線上訂單（店內端）API 輸入輸出（docs/44 §5.3）。金額一律整數元字串。"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, PlainSerializer

from app.core.money import format_ntd
from app.modules.onlineorder.models import OnlineOrder
from app.modules.onlineorder.orders_service import OnlineCart, OnlineOrdersOverview

NTDOut = Annotated[Decimal, PlainSerializer(format_ntd, return_type=str)]


class OnlineOrderLineRead(BaseModel):
    line_no: int
    item_id: int
    name: str
    option_ids: list[int]
    unit_price: int
    qty: int
    line_total: int
    limited: bool


class OnlineOrderRead(BaseModel):
    id: int
    remote_id: str
    table_label: str | None
    service_mode: str
    total: NTDOut
    payment_method: str
    note: str | None
    lines: list[OnlineOrderLineRead]
    created_at: datetime
    """客人送單的時間。"""
    sync_status: str
    hold_status: str
    payment_status: str
    reject_reason: str | None
    sale_id: int | None

    @classmethod
    def from_row(cls, row: OnlineOrder) -> "OnlineOrderRead":
        return cls(
            id=row.id,
            remote_id=row.remote_id,
            table_label=row.table_label,
            service_mode=row.service_mode,
            total=row.total,
            payment_method=row.payment_method,
            note=row.note,
            lines=[OnlineOrderLineRead.model_validate(line) for line in row.lines],
            created_at=row.remote_created_at,
            sync_status=row.sync_status,
            hold_status=row.hold_status,
            payment_status=row.payment_status,
            reject_reason=row.reject_reason,
            sale_id=row.sale_id,
        )


class OnlineOrdersRead(BaseModel):
    configured: bool
    accepting: bool | None
    """雲端說的接單狀態；還沒拉過單是 null。"""
    paused_reason: str | None
    last_pull_at: datetime | None
    last_pull_error: str | None
    orders: list[OnlineOrderRead]

    @classmethod
    def from_overview(cls, o: OnlineOrdersOverview) -> "OnlineOrdersRead":
        link = o.link
        return cls(
            configured=o.configured,
            accepting=None if link is None else link.accepting,
            paused_reason=None if link is None else link.paused_reason,
            last_pull_at=None if link is None else link.last_pull_at,
            last_pull_error=None if link is None else link.last_pull_error,
            orders=[OnlineOrderRead.from_row(row) for row in o.orders],
        )


class OnlineCartLineRead(BaseModel):
    menu_item_id: int
    menu_option_ids: list[int]
    qty: int
    description: str
    online_unit_price: NTDOut
    """客人送單時的單價。"""
    unit_price: NTDOut
    """POS 目前的單價（結帳照這個收）。"""


class OnlineCartRead(BaseModel):
    order_id: int
    service_mode: str
    table_no: str | None
    note: str | None
    lines: list[OnlineCartLineRead]
    online_total: NTDOut
    total: NTDOut

    @classmethod
    def from_cart(cls, cart: OnlineCart) -> "OnlineCartRead":
        order = cart.order
        return cls(
            order_id=order.id,
            service_mode=order.service_mode,
            table_no=order.table_label if order.service_mode == "DINE_IN" else None,
            note=order.note,
            lines=[
                OnlineCartLineRead(
                    menu_item_id=line.menu_item_id,
                    menu_option_ids=line.menu_option_ids,
                    qty=line.qty,
                    description=line.description,
                    online_unit_price=line.online_unit_price,
                    unit_price=line.unit_price,
                )
                for line in cart.lines
            ],
            online_total=cart.online_total,
            total=cart.total,
        )


class OnlineAcceptingRequest(BaseModel):
    accepting: bool
