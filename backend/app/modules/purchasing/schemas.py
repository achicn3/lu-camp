"""purchasing API schema：供應商、採購單與收貨結果。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator
from sqlalchemy import inspect

from app.core.money import ensure_ntd_fits_numeric_12, format_ntd
from app.modules.purchasing.models import GoodsReceipt, PurchaseOrder, PurchaseOrderLine
from app.shared.enums import PurchaseOrderStatus

NTDAmount = Annotated[Decimal, PlainSerializer(format_ntd, return_type=str)]


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    contact: str | None = Field(default=None, max_length=200)
    tax_id: str | None = Field(default=None, max_length=20)


class SupplierUpdate(BaseModel):
    """編輯供應商（稀疏 PATCH）：只更新有帶的欄位。

    未帶的欄位維持原值（省略 ≠ 清空，Codex 對抗審 medium）；帶 null 才是明確清空聯絡方式/統編。
    名稱有帶時不可為空白。
    """

    name: str | None = Field(default=None, min_length=1, max_length=150)
    contact: str | None = Field(default=None, max_length=200)
    tax_id: str | None = Field(default=None, max_length=20)


class SupplierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    name: str
    contact: str | None
    tax_id: str | None
    is_active: bool
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
        ensure_ntd_fits_numeric_12(value, field="unit_cost ")
        return value


class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    lines: list[PurchaseOrderLineCreate] = Field(min_length=1)
    # False（預設）建為草稿；True 建立即送出（ORDERED、計入待到貨、可收貨）。
    submit: bool = False


class PurchaseOrderLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    catalog_product_id: int
    qty: int
    received_qty: int
    unit_cost: NTDAmount
    line_total: NTDAmount

    @classmethod
    def from_model(cls, line: PurchaseOrderLine) -> "PurchaseOrderLineRead":
        return cls.model_validate(
            {
                "id": line.id,
                "catalog_product_id": line.catalog_product_id,
                "qty": line.qty,
                "received_qty": line.received_qty,
                "unit_cost": line.unit_cost,
                "line_total": Decimal(line.qty) * line.unit_cost,
            }
        )


class PurchaseOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    supplier_id: int
    supplier_name: str  # 下單當下的供應商名快照（改名/停用不影響歷史顯示）
    status: PurchaseOrderStatus
    ordered_by: int
    ordered_at: datetime
    received_at: datetime | None
    received_by: int | None
    created_at: datetime
    updated_at: datetime
    total_cost: NTDAmount
    lines: list[PurchaseOrderLineRead]
    # 各收貨批次（分批收貨；每批可各自選填進項發票）。未收貨 → 空陣列。
    receipts: list["GoodsReceiptRead"] = []

    @classmethod
    def from_model(cls, purchase_order: PurchaseOrder) -> "PurchaseOrderRead":
        lines = [PurchaseOrderLineRead.from_model(line) for line in purchase_order.lines]
        total = sum((line.line_total for line in lines), Decimal(0))
        # receipts 為 selectin 關聯：SELECT 載入的 PO 已就緒；**剛 add/flush 的新 PO** 尚未
        # 載入（同步 context 讀取會觸發 lazy IO → MissingGreenlet），而新建 PO 必無收貨單，
        # 以 unloaded 檢查安全視為空。
        insp = inspect(purchase_order)
        receipts = (
            []
            if "receipts" in insp.unloaded
            else [GoodsReceiptRead.from_model(r) for r in purchase_order.receipts]
        )
        return cls.model_validate(
            {
                "receipts": receipts,
                "id": purchase_order.id,
                "store_id": purchase_order.store_id,
                "supplier_id": purchase_order.supplier_id,
                "supplier_name": purchase_order.supplier_name,
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

    號碼＝2 英文大寫＋8 數字；三個金額照錄供應商原始發票，後端只驗證皆為整數元且
    invoice_net + invoice_tax = invoice_total，不以補登當下設定重算歷史憑證。
    """

    invoice_number: str = Field(pattern=r"^[A-Z]{2}[0-9]{8}$")
    invoice_date: date
    invoice_net: NTDAmount
    invoice_tax: NTDAmount
    invoice_total: NTDAmount

    @field_validator("invoice_net", "invoice_tax", "invoice_total")
    @classmethod
    def _whole_nonnegative(cls, v: Decimal) -> Decimal:
        if v < 0 or v != v.to_integral_value():
            raise ValueError("發票金額必須為非負整數元")
        ensure_ntd_fits_numeric_12(v, field="發票金額")
        return Decimal(v.to_integral_value())

    @model_validator(mode="after")
    def _amounts_match_original_invoice(self) -> "InputInvoiceIn":
        if self.invoice_total <= 0:
            raise ValueError("發票總額必須為正整數元")
        if self.invoice_net + self.invoice_tax != self.invoice_total:
            raise ValueError("原始發票未稅額＋稅額必須等於總額")
        return self


class ReceiveLineIn(BaseModel):
    """本次收貨的單一明細實收量（不得超過該明細待收 qty − received_qty）。"""

    line_id: int
    qty: int = Field(gt=0)


class ReceivePurchaseOrderRequest(BaseModel):
    """分批收貨請求：各明細本次實收量＋選填進項發票（供應商發票隨貨時一併登錄）。"""

    lines: list[ReceiveLineIn] = Field(min_length=1)
    invoice: InputInvoiceIn | None = None


class InputInvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    invoice_number: str
    invoice_date: date
    invoice_total: NTDAmount
    invoice_net: NTDAmount
    invoice_tax: NTDAmount


class GoodsReceiptRead(BaseModel):
    """單一收貨批次（分批收貨事件）＋其選填進項發票。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    received_at: datetime
    received_by: int
    invoice: InputInvoiceRead | None = None

    @classmethod
    def from_model(cls, receipt: "GoodsReceipt") -> "GoodsReceiptRead":
        invoice = (
            InputInvoiceRead.model_validate(receipt) if receipt.invoice_number is not None else None
        )
        return cls.model_validate(
            {
                "id": receipt.id,
                "received_at": receipt.received_at,
                "received_by": receipt.received_by,
                "invoice": invoice,
            }
        )


class ReceivePurchaseOrderResult(BaseModel):
    receipt_id: int
    purchase_order: PurchaseOrderRead
