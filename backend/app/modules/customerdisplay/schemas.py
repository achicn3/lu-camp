"""客顯裝置、櫃檯與配對 API schema。"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

from app.modules.sales.inputs import SaleLineInput
from app.shared.enums import CartSessionStatus, SaleLineType, TenderType


class KioskDeviceLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)
    installation_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    label: str = Field(min_length=1, max_length=100)


class KioskSummary(BaseModel):
    id: int
    label: str


class TerminalSummary(BaseModel):
    id: int
    name: str


class KioskDeviceSessionRead(BaseModel):
    device_id: int
    label: str
    csrf_token: str
    pairing_code: str | None
    pairing_code_expires_at: datetime | None
    paired_terminal: TerminalSummary | None


class KioskDeviceRead(BaseModel):
    device_id: int
    label: str
    pairing_code: str | None
    pairing_code_expires_at: datetime | None
    paired_terminal: TerminalSummary | None


class KioskHeartbeatRequest(BaseModel):
    current_session_id: int | None = Field(default=None, ge=1)
    displayed_revision: Annotated[int, Field(ge=0)]


class KioskHeartbeatRead(BaseModel):
    online: bool
    last_seen_at: datetime


class TerminalCreateRequest(BaseModel):
    installation_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    name: str = Field(min_length=1, max_length=100)


class TerminalRead(BaseModel):
    id: int
    installation_id: str
    name: str
    paired_kiosk: KioskSummary | None


class TerminalPairRequest(BaseModel):
    pairing_code: str = Field(pattern=r"^\d{6}$")


class TerminalUnpairRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class CartLineRequest(BaseModel):
    line_type: SaleLineType
    item_code: str | None = Field(default=None, min_length=1, max_length=64)
    catalog_product_id: int | None = Field(default=None, ge=1)
    bulk_lot_id: int | None = Field(default=None, ge=1)
    menu_item_id: int | None = Field(default=None, ge=1)
    qty: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _matching_reference(self) -> "CartLineRequest":
        refs = {
            SaleLineType.SERIALIZED: self.item_code,
            SaleLineType.CATALOG: self.catalog_product_id,
            SaleLineType.BULK_LOT: self.bulk_lot_id,
            SaleLineType.MENU: self.menu_item_id,
        }
        if refs[self.line_type] is None:
            raise ValueError(f"{self.line_type.value} 明細缺少對應商品識別")
        if self.line_type is SaleLineType.SERIALIZED and self.qty != 1:
            raise ValueError("序號品數量固定為 1")
        return self

    def to_input(self) -> SaleLineInput:
        return SaleLineInput(
            line_type=self.line_type,
            item_code=self.item_code,
            catalog_product_id=self.catalog_product_id,
            bulk_lot_id=self.bulk_lot_id,
            menu_item_id=self.menu_item_id,
            qty=self.qty,
        )


class CartTenderRequest(BaseModel):
    tender_type: TenderType
    amount: Decimal = Field(gt=0)

    @field_validator("amount")
    @classmethod
    def _whole_ntd(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("付款金額必須為整數元")
        return value


class CartUpsertRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)
    lines: list[CartLineRequest] = Field(min_length=1)
    buyer_contact_id: int | None = Field(default=None, ge=1)
    tenders: list[CartTenderRequest] | None = Field(default=None, min_length=1, max_length=2)


class CartCancelRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=200)


class CartItemRead(BaseModel):
    item_key: str
    line_type: SaleLineType
    name: str
    qty: int
    unit_price: str
    original_unit_price: str | None
    discount_amount: str
    line_total: str


class MaskedMemberRead(BaseModel):
    display_name: str


class CartTenderRead(BaseModel):
    tender_type: TenderType
    amount: str


class CartSnapshotRead(BaseModel):
    content_version: str
    items: list[CartItemRead]
    total: str
    discount_total: str
    campaign_name: str | None
    member: MaskedMemberRead | None
    tenders: list[CartTenderRead]


class CartChangeRead(BaseModel):
    type: str
    item_key: str
    name: str
    from_qty: int | None = None
    to_qty: int | None = None


class CartSessionRead(BaseModel):
    id: int
    status: CartSessionStatus
    revision: int
    pos_terminal_id: int
    kiosk_device_id: int
    snapshot: CartSnapshotRead
    changes: list[CartChangeRead]
    created_at: datetime
    updated_at: datetime
