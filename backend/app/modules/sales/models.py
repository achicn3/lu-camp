"""sales 模型：銷售單與明細（docs/03）。

每張表帶 store_id。金額 NUMERIC(scale 0) → Decimal（NT$ 整數元）：subtotal=未稅、tax=稅額、
total=含稅總額（= Σ 明細 line_total）。invoice_id 待 T13（einvoice）建 invoices 表後再加 FK。
列舉以 native_enum=False + CHECK 儲存。
"""

from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import (
    PaymentMethod,
    SaleInvoiceStatus,
    SaleLineType,
    SaleStatus,
    TenderType,
)


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class Sale(Base, TimestampMixin):
    """銷售單。建立時即 COMPLETED；本階段一律 invoice_status=NOT_ISSUED（開票於 T13）。

    idempotency_key：由結帳端產生，(store_id, idempotency_key) 唯一，防網路重試重複建單/收錢
    （D-2）。同一 key 重送回原單、不重跑副作用。NULL 不受唯一限制（領域層直接呼叫可不帶）。
    idempotency_fingerprint：購物車內容的 sha256；重播時比對，同 key 但內容不同 → 拒絕（避免
    誤用/重用 key 把不同購物車的結帳靜默丟掉）。
    """

    __tablename__ = "sales"
    __table_args__ = (
        UniqueConstraint("store_id", "idempotency_key", name="uq_sales_store_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(64))
    clerk_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    buyer_contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    tax: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    # 結帳時實際累積的會員點數（docs/16 §0）；void 以此沖回、不重算（歷史單為 0）。
    awarded_points: Mapped[int] = mapped_column(default=0, server_default=text("0"))
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


class SaleTender(Base, TimestampMixin):
    """銷售收款明細（SC-3，docs/16 §1.6）。一筆 sale 一到多列，Σ amount = sales.total。

    CASH 走錢櫃 SALE_IN（現金部分）；STORE_CREDIT 走帳本 DEBIT（不碰現金，I-9）。
    每種 tender_type 一筆 sale 至多一列（與帳本「同 SALE 來源至多一筆 DEBIT」一致）。
    """

    __tablename__ = "sale_tenders"
    __table_args__ = (
        UniqueConstraint("sale_id", "tender_type", name="uq_sale_tenders_sale_type"),
        CheckConstraint("amount > 0", name="ck_sale_tenders_amount_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), index=True)
    tender_type: Mapped[TenderType] = mapped_column(_enum_col(TenderType))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
