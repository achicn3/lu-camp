"""inventory 唯讀查詢 schema（T19-pre-B）。

金額以字串傳輸（§11）、新台幣整數元（§6）。序號品一般查詢**不含收購成本**
（成本屬敏感營業資訊，POS 查件不需要）；散裝堆含成本（docs/10 §5 /inventory
明列各堆顯示收購成本與售出進度）。
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer

from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    Grade,
    OwnershipType,
    SerializedItemStatus,
)

NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]


class SerializedItemRead(BaseModel):
    """序號品輸出（POS 掃碼查件/庫存列表；不含 acquisition_cost）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    item_code: str
    name: str
    brand_id: int | None
    product_model_id: int | None
    grade: Grade
    ownership_type: OwnershipType
    consignor_id: int | None
    commission_pct: int | None
    listed_price: NTDAmount
    status: SerializedItemStatus
    intake_date: datetime
    sold_date: datetime | None


class CatalogProductRead(BaseModel):
    """數量型商品輸出（POS 選件/庫存列表）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    sku: str
    name: str
    brand_id: int | None
    unit_price: NTDAmount
    quantity_on_hand: int
    reorder_point: int


class BulkLotRead(BaseModel):
    """散裝堆輸出（POS 明確選堆/庫存列表；含收購成本與售出進度）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    lot_code: str
    label: str | None
    name: str
    brand_id: int | None
    grade: Grade
    acquisition_cost: NTDAmount
    acquisition_basis: BulkAcquisitionBasis
    unit_price: NTDAmount
    total_qty: int
    remaining_qty: int
    status: BulkLotStatus
