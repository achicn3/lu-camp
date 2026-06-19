"""purchasing 模型：供應商、採購單、採購明細與收貨紀錄。

第一版只處理店內補貨用的數量型商品（catalog_products），不處理發票、應付帳款或部分收貨。
金額一律 NUMERIC(scale 0) → Decimal（NT$ 整數元）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.shared.enums import PurchaseOrderStatus


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class Supplier(Base, TimestampMixin):
    """店內供應商主檔。"""

    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("store_id", "name", name="uq_suppliers_store_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    contact: Mapped[str | None] = mapped_column(String(200))
    tax_id: Mapped[str | None] = mapped_column(String(20))


class PurchaseOrder(Base, TimestampMixin):
    """採購單。第一版建立後即 ORDERED；收貨一次後轉 RECEIVED。"""

    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        _enum_col(PurchaseOrderStatus),
        default=PurchaseOrderStatus.ORDERED,
        server_default=PurchaseOrderStatus.ORDERED.value,
    )
    ordered_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PurchaseOrderLine.id",
    )


class PurchaseOrderLine(Base):
    """採購明細。只允許 catalog_product_id；序號品/散裝品不走此流程。"""

    __tablename__ = "purchase_order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    purchase_order_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    catalog_product_id: Mapped[int] = mapped_column(ForeignKey("catalog_products.id"), index=True)
    qty: Mapped[int] = mapped_column()
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 0))

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class GoodsReceipt(Base):
    """採購收貨紀錄。第一版一張 PO 最多一筆 receipt。"""

    __tablename__ = "goods_receipts"
    __table_args__ = (
        UniqueConstraint("purchase_order_id", name="uq_goods_receipts_purchase_order_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    received_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
