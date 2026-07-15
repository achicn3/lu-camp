"""purchasing 模型：供應商、採購單、採購明細與收貨紀錄。

只處理店內補貨用的數量型商品（catalog_products），不處理應付帳款。
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
from app.shared.enums import PurchaseOrderStatus


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class Supplier(Base, TimestampMixin):
    """店內供應商主檔。

    is_active＝啟用中；停用（False）者不出現在建單供應商選單，但保留供既有採購單歷史參照。
    """

    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("store_id", "name", name="uq_suppliers_store_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    contact: Mapped[str | None] = mapped_column(String(200))
    tax_id: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class PurchaseOrder(Base, TimestampMixin):
    """採購單。DRAFT→ORDERED→PARTIAL→RECEIVED；DRAFT/ORDERED 可取消為 CANCELLED。

    received_at/received_by 於**全部收足**（RECEIVED）時記錄；部分到貨期間為 None，
    各批收貨時間見各 GoodsReceipt.received_at。
    """

    __tablename__ = "purchase_orders"

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


class GoodsReceipt(Base):
    """採購收貨紀錄（一次收貨事件）。分批收貨下一張 PO 可有多筆 receipt，各自選填進項發票。"""

    __tablename__ = "goods_receipts"
    __table_args__ = (
        # 進項發票一致性：全空（未登錄）或全備；金額守恆 net + tax = total。
        CheckConstraint(
            "(invoice_number IS NULL AND invoice_date IS NULL AND invoice_total IS NULL"
            " AND invoice_net IS NULL AND invoice_tax IS NULL)"
            " OR (invoice_number IS NOT NULL AND invoice_date IS NOT NULL"
            " AND invoice_total IS NOT NULL AND invoice_net IS NOT NULL"
            " AND invoice_tax IS NOT NULL AND invoice_net + invoice_tax = invoice_total)",
            name="ck_goods_receipts_invoice_consistent",
        ),
        # 號碼格式：2 英文大寫＋8 數字（台灣統一發票字軌）。
        CheckConstraint(
            "invoice_number IS NULL OR invoice_number ~ '^[A-Z]{2}[0-9]{8}$'",
            name="ck_goods_receipts_invoice_number_format",
        ),
        # 同店同號同日的實體發票只能入帳一次（Codex 第一輪 high：重複登錄會虛增進貨/進項稅且
        # 不可覆寫難以回復）；字軌跨期回收屬不同日期、不受此限。
        Index(
            "uq_goods_receipts_store_invoice",
            "store_id",
            "invoice_number",
            "invoice_date",
            unique=True,
            postgresql_where=text("invoice_number IS NOT NULL"),
        ),
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
    # 進項發票（裁示 2026-07-11）：供應商開立的發票於**收貨時**選填登錄（漏登可事後補登一次）。
    # 號碼＝2 英文＋8 數字；金額整數元；net＋tax 由 total 以 split_tax_inclusive 拆分（§6），
    # DB CHECK 守恆與一致性（要嘛全空、要嘛號碼/日期/三金額齊備且 net+tax=total）。
    invoice_number: Mapped[str | None] = mapped_column(String(10))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    invoice_total: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    invoice_net: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    invoice_tax: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    # 分批收貨冪等鍵＋請求指紋（同 key 重送回原結果、不同 payload → 409）。
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
