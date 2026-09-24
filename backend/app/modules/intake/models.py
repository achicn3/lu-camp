"""收購佇列模型（docs/42）：現場收件、估價、確認；付款與上架屬後續各期。

一位客人一批（一個當日 A 編號、一張收件單條碼），一批多列；每列是一種商品（同款同狀況同價才合併）。
列一旦進入「待確認」就不刪除：之後查得到當時收了什麼、退了什麼。
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import AcquisitionType, Grade, IntakeBatchStatus, IntakeDisposition


def _enum_col(enum_cls: type) -> Enum:
    """非原生 enum＋CHECK（與其他模組一致；只用 String 會讓讀回來的不是 enum）。"""
    return Enum(enum_cls, native_enum=False, length=30, create_constraint=True)


class IntakeBatch(Base, TimestampMixin):
    """一位客人帶來的一批商品。ticket_no 是同店同一台北營業日內的流水號（畫面顯示 A001）。"""

    __tablename__ = "intake_batches"
    __table_args__ = (
        Index(
            "uq_intake_batches_store_date_no", "store_id", "ticket_date", "ticket_no", unique=True
        ),
        Index("ix_intake_batches_store_status", "store_id", "status"),
        CheckConstraint("declared_item_count >= 1", name="ck_intake_batches_declared_pos"),
        # 取消的三個欄位同進同出：不會有「取消了但不知道誰、為什麼」。
        CheckConstraint(
            "(cancelled_at IS NULL AND cancelled_by_user_id IS NULL AND cancel_reason IS NULL)"
            " OR (cancelled_at IS NOT NULL AND cancelled_by_user_id IS NOT NULL"
            " AND cancel_reason IS NOT NULL)",
            name="ck_intake_batches_cancel_shape",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    # 台北營業日（core.time.store_date）：每天重編的依據。
    ticket_date: Mapped[date] = mapped_column(Date)
    ticket_no: Mapped[int] = mapped_column()
    # 報到時和客人一起點清的實收件數（簽署一律用實際成交件數，這個只留作對照）。
    declared_item_count: Mapped[int] = mapped_column()
    status: Mapped[IntakeBatchStatus] = mapped_column(_enum_col(IntakeBatchStatus))
    note: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancel_reason: Mapped[str | None] = mapped_column(String(200))


class IntakeLine(Base, TimestampMixin):
    """批次裡的一列估價。金額皆含稅整數元、**每件**；總額由服務層依件數算。"""

    __tablename__ = "intake_lines"
    __table_args__ = (
        UniqueConstraint("batch_id", "line_no", name="uq_intake_lines_batch_no"),
        CheckConstraint("qty >= 1", name="ck_intake_lines_qty_pos"),
        CheckConstraint(
            "accepted_qty >= 0 AND accepted_qty <= qty", name="ck_intake_lines_accepted_range"
        ),
        CheckConstraint(
            "discount_pct IS NULL OR discount_pct BETWEEN 1 AND 100",
            name="ck_intake_lines_discount_range",
        ),
        CheckConstraint(
            "commission_pct IS NULL OR commission_pct BETWEEN 0 AND 100",
            name="ck_intake_lines_commission_range",
        ),
        CheckConstraint(
            "(reference_price IS NULL OR reference_price >= 0)"
            " AND (expected_listed_price IS NULL OR expected_listed_price >= 0)"
            " AND (suggested_cost IS NULL OR suggested_cost >= 0)"
            " AND (deal_cost IS NULL OR deal_cost >= 0)",
            name="ck_intake_lines_amounts_nonneg",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("intake_batches.id"), index=True)
    line_no: Mapped[int] = mapped_column()
    short_name: Mapped[str] = mapped_column(String(100))
    qty: Mapped[int] = mapped_column()
    acquisition_type: Mapped[AcquisitionType] = mapped_column(_enum_col(AcquisitionType))
    reference_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    # 預計售價折數（十分位整數，65＝6.5 折；五折＝預計賣原價的一半，不是用一半收購）。
    discount_pct: Mapped[int | None] = mapped_column()
    expected_listed_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    # 系統建議與最後成交都留（裁示 5：店員可改成交價）。寄售沒有收購價。
    suggested_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    deal_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    commission_pct: Mapped[int | None] = mapped_column()
    grade: Mapped[Grade | None] = mapped_column(_enum_col(Grade))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id"))
    product_model_id: Mapped[int | None] = mapped_column(ForeignKey("product_models.id"))
    note: Mapped[str | None] = mapped_column(String(500))
    disposition: Mapped[IntakeDisposition] = mapped_column(
        _enum_col(IntakeDisposition),
        default=IntakeDisposition.PENDING,
        server_default=IntakeDisposition.PENDING.value,
    )
    accepted_qty: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    # 沒成交的件（qty − accepted_qty）是否已交還客人；取消整批也逐列記。
    returned_to_customer: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
