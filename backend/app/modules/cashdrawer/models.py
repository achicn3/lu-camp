"""cashdrawer 模型：現金抽屜班別與現金異動。

cash_session 以 partial unique index 確保同一 store 至多一個 OPEN（靠約束擋，非先查再開）。
金額一律 NUMERIC(scale 0) → Decimal（NT$ 整數元）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.shared.enums import CashMovementType, CashSessionStatus


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class CashSession(Base):
    """現金抽屜班別。同一 store 同時只允許一個 OPEN。"""

    __tablename__ = "cash_sessions"
    __table_args__ = (
        Index(
            "uq_one_open_cash_session_per_store",
            "store_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    opened_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    opening_float: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    status: Mapped[CashSessionStatus] = mapped_column(
        _enum_col(CashSessionStatus),
        default=CashSessionStatus.OPEN,
        server_default=CashSessionStatus.OPEN.value,
    )
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counted_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    variance: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))


class CashMovement(Base):
    """現金異動（append-only 帳；無 updated_at）。"""

    __tablename__ = "cash_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("cash_sessions.id"), index=True)
    type: Mapped[CashMovementType] = mapped_column(_enum_col(CashMovementType))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    ref_type: Mapped[str | None] = mapped_column(String(50))
    ref_id: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
