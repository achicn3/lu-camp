"""sales 模型：銷售單與明細（docs/03）。

每張表帶 store_id。金額 NUMERIC(scale 0) → Decimal（NT$ 整數元）：subtotal=未稅、tax=稅額、
total=含稅總額（= Σ 明細 line_total）。invoice_id 待 T13（einvoice）建 invoices 表後再加 FK。
列舉以 native_enum=False + CHECK 儲存。
"""

from decimal import Decimal

from sqlalchemy import Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import PaymentMethod, SaleInvoiceStatus, SaleLineType, SaleStatus


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class Sale(Base, TimestampMixin):
    """銷售單。建立時即 COMPLETED；本階段一律 invoice_status=NOT_ISSUED（開票於 T13）。"""

    __tablename__ = "sales"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    clerk_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    buyer_contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    tax: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    payment_method: Mapped[PaymentMethod] = mapped_column(
        _enum_col(PaymentMethod),
        default=PaymentMethod.CASH,
        server_default=PaymentMethod.CASH.value,
    )
    invoice_status: Mapped[SaleInvoiceStatus] = mapped_column(
        _enum_col(SaleInvoiceStatus),
        default=SaleInvoiceStatus.NOT_ISSUED,
        server_default=SaleInvoiceStatus.NOT_ISSUED.value,
    )
    status: Mapped[SaleStatus] = mapped_column(
        _enum_col(SaleStatus),
        default=SaleStatus.COMPLETED,
        server_default=SaleStatus.COMPLETED.value,
    )


class SaleLine(Base, TimestampMixin):
    """銷售明細行。依 line_type 指向 serialized / catalog / bulk_lot 其一。"""

    __tablename__ = "sale_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), index=True)
    line_type: Mapped[SaleLineType] = mapped_column(_enum_col(SaleLineType))
    serialized_item_id: Mapped[int | None] = mapped_column(ForeignKey("serialized_items.id"))
    catalog_product_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_products.id"))
    bulk_lot_id: Mapped[int | None] = mapped_column(ForeignKey("bulk_lots.id"))
    description: Mapped[str] = mapped_column(String(150))
    qty: Mapped[int] = mapped_column()
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 0))
