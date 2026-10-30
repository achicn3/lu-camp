"""purchasing 模型：供應商、採購單、採購明細與收貨紀錄。

只處理店內補貨用的一般商品（catalog_products），不處理應付帳款。
支援**分批收貨**：每明細記已收數量（received_qty），一張採購單可多次收貨（多筆 goods_receipts）。
金額一律 NUMERIC(scale 0) → Decimal（NT$ 整數元）。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.shared.enums import PurchaseOrderStatus, SupplierPaymentTerms

# 付款條件與數字要一致（docs/99 §5.2）：先付、貨到付款不填數字；月結填每月幾號；到貨後填幾天。
PAYMENT_TERMS_CHECK = (
    "(payment_terms IS NULL AND payment_terms_value IS NULL)"
    " OR (payment_terms IN ('PREPAID', 'COD') AND payment_terms_value IS NULL)"
    " OR (payment_terms = 'MONTHLY' AND payment_terms_value BETWEEN 1 AND 31)"
    " OR (payment_terms = 'NET_DAYS' AND payment_terms_value BETWEEN 0 AND 365)"
)


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class Supplier(Base, TimestampMixin):
    """店內供應商主檔。

    is_active＝啟用中；停用（False）者不出現在建單供應商選單，但保留供既有採購單歷史參照。
    payment_terms＝預設付款條件（限管理者設定）；採購單送出時快照過去，之後改不影響舊單。
    """

    __tablename__ = "suppliers"
    __table_args__ = (
        UniqueConstraint("store_id", "name", name="uq_suppliers_store_name"),
        CheckConstraint(PAYMENT_TERMS_CHECK, name="ck_suppliers_payment_terms"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    contact: Mapped[str | None] = mapped_column(String(200))
    tax_id: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    payment_terms: Mapped[SupplierPaymentTerms | None] = mapped_column(
        _enum_col(SupplierPaymentTerms)
    )
    payment_terms_value: Mapped[int | None] = mapped_column()


class PurchaseOrder(Base, TimestampMixin):
    """採購單。DRAFT→ORDERED→PARTIAL→RECEIVED；DRAFT/ORDERED 可取消為 CANCELLED。

    received_at/received_by 於**全部收足**（RECEIVED）時記錄；部分到貨期間為 None，
    各批收貨時間見各 GoodsReceipt.received_at。

    日期（docs/99 §5）：ordered_on＝實際下單日（送出時定，可選過去；ordered_at 仍是按送出的那一刻，
    留作稽核）；expected_on＝預計到貨日。payment_terms*＝送出當下快照的供應商付款條件；
    付款期限由快照與日期即時算出，payment_due_override＝管理者手動改的期限。
    """

    __tablename__ = "purchase_orders"
    __table_args__ = (
        CheckConstraint(PAYMENT_TERMS_CHECK, name="ck_purchase_orders_payment_terms"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    # 下單當下的供應商名快照：歷史顯示/搜尋用此，供應商改名不回溯改寫歷史單。
    supplier_name: Mapped[str] = mapped_column(String(150))
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        _enum_col(PurchaseOrderStatus),
        default=PurchaseOrderStatus.DRAFT,
        server_default=PurchaseOrderStatus.DRAFT.value,
    )
    ordered_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    ordered_on: Mapped[date | None] = mapped_column(Date)
    expected_on: Mapped[date | None] = mapped_column(Date)
    payment_terms: Mapped[SupplierPaymentTerms | None] = mapped_column(
        _enum_col(SupplierPaymentTerms)
    )
    payment_terms_value: Mapped[int | None] = mapped_column()
    payment_due_override: Mapped[date | None] = mapped_column(Date)

    receipts: Mapped[list["GoodsReceipt"]] = relationship(
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="GoodsReceipt.id",
    )
    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        back_populates="purchase_order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="PurchaseOrderLine.id",
    )


class PurchaseOrderLine(Base):
    """採購明細。只允許 catalog_product_id；序號品/散裝品不走此流程。

    received_qty：累計已收數量（分批收貨累加）；0 <= received_qty <= qty。
    """

    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        CheckConstraint(
            "received_qty >= 0 AND received_qty <= qty",
            name="ck_purchase_order_lines_received_qty_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    purchase_order_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    catalog_product_id: Mapped[int] = mapped_column(ForeignKey("catalog_products.id"), index=True)
    qty: Mapped[int] = mapped_column()
    received_qty: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 0))

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class InputInvoice(Base, TimestampMixin):
    """進項發票（供應商開給店家的發票；docs/70 §5、ADR-030）。

    獨立於收貨：一張發票可涵蓋多批收貨（跨採購單、須同一供應商），收貨後隔月才開也能事後登錄。
    三個金額照錄原始發票，DB CHECK 守恆（未稅＋稅額＝含稅）。同店同號同日的實體發票只能入帳一次。
    """

    __tablename__ = "purchase_input_invoices"
    __table_args__ = (
        CheckConstraint(
            "invoice_net >= 0 AND invoice_tax >= 0 AND invoice_total > 0"
            " AND invoice_net + invoice_tax = invoice_total",
            name="ck_purchase_input_invoices_amounts",
        ),
        # 號碼格式：2 英文大寫＋8 數字（台灣統一發票字軌）。
        CheckConstraint(
            "invoice_number ~ '^[A-Z]{2}[0-9]{8}$'",
            name="ck_purchase_input_invoices_number_format",
        ),
        # 重複登錄會虛增進貨／進項稅；字軌跨期回收屬不同日期、不受此限。
        UniqueConstraint(
            "store_id",
            "invoice_number",
            "invoice_date",
            name="uq_purchase_input_invoices_store_number_date",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), index=True)
    # 登錄當下的供應商名快照（申報月報顯示用；供應商改名不改寫歷史）。
    supplier_name: Mapped[str] = mapped_column(String(150))
    invoice_number: Mapped[str] = mapped_column(String(10))
    invoice_date: Mapped[date] = mapped_column(Date)
    invoice_total: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    invoice_net: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    invoice_tax: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))

    receipts: Mapped[list["GoodsReceipt"]] = relationship(
        back_populates="input_invoice", lazy="selectin", order_by="GoodsReceipt.id"
    )


class GoodsReceipt(Base):
    """採購收貨紀錄（一次收貨事件）。分批收貨下一張 PO 可有多筆 receipt；
    每批最多掛一張進項發票（`input_invoice_id`），一張發票可掛多批。"""

    __tablename__ = "goods_receipts"
    __table_args__ = (
        # 分批收貨冪等：同店同 Idempotency-Key 只成立一筆收貨（防網路重試重複入庫）。
        Index(
            "uq_goods_receipts_store_idempotency",
            "store_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    purchase_order: Mapped["PurchaseOrder"] = relationship(back_populates="receipts")
    received_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    input_invoice_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_input_invoices.id"), index=True
    )
    input_invoice: Mapped[InputInvoice | None] = relationship(
        back_populates="receipts", lazy="selectin"
    )
    # 分批收貨冪等鍵＋請求指紋（同 key 重送回原結果、不同 payload → 409）。
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
