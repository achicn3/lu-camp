"""returns 模型：退貨主檔與明細。

第一版支援銷售退貨的 append-only 紀錄，副作用（退現、回補庫存、結算反轉）在 service
同一交易內完成；不刪除原 sale / sale_line。

租戶完整性（§4）以 DB 層複合 FK 守護（比照 sale_tenders 的 (sale_id, store_id) 綁定）：
退貨單與其銷售同店、退貨明細與其退貨單同店、退貨明細與其銷售明細同店——跨店引用在
DB 層即被擋下，不全靠 service。idempotency_key（(store_id, key) 唯一）防雙擊/網路重試
重複退貨重複退現（比照 sales D-2）。
"""

from decimal import Decimal

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin


class CustomerReturn(Base, TimestampMixin):
    """退貨主檔。created_at 即退貨時間；refund_amount 為本次退貨含稅退款總額。"""

    __tablename__ = "returns"
    __table_args__ = (
        # (store_id, idempotency_key) 唯一：同 key 重送只建一筆、回原單（防重複退現）。
        UniqueConstraint("store_id", "idempotency_key", name="uq_returns_store_idempotency_key"),
        # 複合租戶鍵：供 return_lines 的 (return_id, store_id) 複合 FK 綁定。
        UniqueConstraint("id", "store_id", name="uq_returns_id_store"),
        # 退貨單必與其銷售同店（DB 層擋跨店退貨）。
        ForeignKeyConstraint(
            ["sale_id", "store_id"],
            ["sales.id", "sales.store_id"],
            name="fk_returns_sale_store",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    sale_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    reason: Mapped[str] = mapped_column(String(500))
    clerk_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(80))
    idempotency_fingerprint: Mapped[str | None] = mapped_column(String(64))

    lines: Mapped[list["ReturnLine"]] = relationship(
        back_populates="customer_return",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ReturnLine.id",
    )


class ReturnLine(Base):
    """退貨明細：指回原 sale_line，保留本次退回數量與金額。"""

    __tablename__ = "return_lines"
    __table_args__ = (
        # 退貨明細必與其退貨單同店。
        ForeignKeyConstraint(
            ["return_id", "store_id"],
            ["returns.id", "returns.store_id"],
            ondelete="CASCADE",
            name="fk_return_lines_return_store",
        ),
        # 退貨明細必與其銷售明細同店（DB 層擋跨店引用 sale_line）。
        ForeignKeyConstraint(
            ["sale_line_id", "store_id"],
            ["sale_lines.id", "sale_lines.store_id"],
            name="fk_return_lines_sale_line_store",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    return_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    sale_line_id: Mapped[int] = mapped_column(index=True)  # 複合租戶 FK 見 __table_args__
    qty: Mapped[int] = mapped_column()
    refund_amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))

    customer_return: Mapped[CustomerReturn] = relationship(back_populates="lines")
