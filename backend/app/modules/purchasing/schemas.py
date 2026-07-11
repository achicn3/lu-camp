"""purchasing API schema：供應商、採購單與收貨結果。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator
from sqlalchemy import inspect

from app.modules.purchasing.models import PurchaseOrder, PurchaseOrderLine
from app.shared.enums import PurchaseOrderStatus

NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    contact: str | None = Field(default=None, max_length=200)
    tax_id: str | None = Field(default=None, max_length=20)


class SupplierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    name: str
    contact: str | None
    tax_id: str | None
    created_at: datetime
    updated_at: datetime


class PurchaseOrderLineCreate(BaseModel):
    catalog_product_id: int
    qty: int = Field(gt=0)
    unit_cost: NTDAmount

    @field_validator("unit_cost")
    @classmethod
    def _positive_whole(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("unit_cost 必須為整數元")
        if value <= 0:
            raise ValueError("unit_cost 必須為正")
        return value


class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    lines: list[PurchaseOrderLineCreate] = Field(min_length=1)


class PurchaseOrderLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    catalog_product_id: int
    qty: int
    unit_cost: NTDAmount
    line_total: NTDAmount

    @classmethod
    def from_model(cls, line: PurchaseOrderLine) -> "PurchaseOrderLineRead":
        return cls.model_validate(
            {
                "id": line.id,
                "catalog_product_id": line.catalog_product_id,
                "qty": line.qty,
                "unit_cost": line.unit_cost,
                "line_total": Decimal(line.qty) * line.unit_cost,
            }
        )


class PurchaseOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    supplier_id: int
    status: PurchaseOrderStatus
    ordered_by: int
    ordered_at: datetime
    received_at: datetime | None
    received_by: int | None
    created_at: datetime
    updated_at: datetime
    total_cost: NTDAmount
    lines: list[PurchaseOrderLineRead]
    # 進項發票（收貨單上；未收貨/未登錄 → None）
    invoice: "InputInvoiceRead | None" = None

    @classmethod
    def from_model(cls, purchase_order: PurchaseOrder) -> "PurchaseOrderRead":
        lines = [PurchaseOrderLineRead.from_model(line) for line in purchase_order.lines]
        total = sum((line.line_total for line in lines), Decimal(0))
        # receipt 為 selectin 關聯：SELECT 載入的 PO 已就緒；**剛 add/flush 的新 PO** 尚未
        # 載入（同步 context 讀取會觸發 lazy IO → MissingGreenlet），而新建 PO 必無收貨單，
        # 以 unloaded 檢查安全視為 None。
        insp = inspect(purchase_order)
        receipt = None if "receipt" in insp.unloaded else purchase_order.receipt
        invoice = (
            InputInvoiceRead.model_validate(receipt)
            if receipt is not None and receipt.invoice_number is not None
            else None
        )
        return cls.model_validate(
            {
                "invoice": invoice,
                "id": purchase_order.id,
                "store_id": purchase_order.store_id,
                "supplier_id": purchase_order.supplier_id,
                "status": purchase_order.status,
                "ordered_by": purchase_order.ordered_by,
                "ordered_at": purchase_order.ordered_at,
                "received_at": purchase_order.received_at,
                "received_by": purchase_order.received_by,
                "created_at": purchase_order.created_at,
                "updated_at": purchase_order.updated_at,
                "total_cost": total,
                "lines": lines,
            }
        )


class InputInvoiceIn(BaseModel):
    """進項發票登錄輸入（裁示 2026-07-11：收貨時選填、漏登可補登一次）。

    號碼＝2 英文大寫＋8 數字；金額為含稅整數元字串（>0）。未稅/稅額由後端以
    settings.tax_rate 用 split_tax_inclusive 拆分（§6），不收前端算的值。
    """

    invoice_number: str = Field(pattern=r"^[A-Z]{2}[0-9]{8}$")
    invoice_date: date
    invoice_total: NTDAmount

    @field_validator("invoice_total")
    @classmethod
    def _positive_whole(cls, v: Decimal) -> Decimal:
        if v <= 0 or v != v.to_integral_value():
            raise ValueError("發票金額必須為正整數元")
        return v


class ReceivePurchaseOrderRequest(BaseModel):
    """收貨請求：進項發票選填（供應商發票通常隨貨送達，收貨時一併登錄）。"""

    invoice: InputInvoiceIn | None = None


class InputInvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invoice_number: str
    invoice_date: date
    invoice_total: NTDAmount
    invoice_net: NTDAmount
    invoice_tax: NTDAmount


class ReceivePurchaseOrderResult(BaseModel):
    receipt_id: int
    purchase_order: PurchaseOrderRead
