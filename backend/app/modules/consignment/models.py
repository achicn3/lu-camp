"""consignment 模型：寄售結算（docs/03）。

售出寄售品時於同一交易建立 PENDING 結算列；付款（→PAID）與退貨反轉（→CANCELLED/
reclaim_needed）屬 Phase 4。金額一律 NUMERIC(scale 0) → Decimal（NT$ 整數元）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import ConsignmentSettlementStatus


class ConsignmentSettlement(Base, TimestampMixin):
    """寄售結算：賣出寄售品時建立（PENDING）。

    抽成金額 = round_ntd(售價 × commission_pct / 100)；應付寄售人 = 售價 − 抽成金額。
    店家收入只認抽成（commission_amount），不認全額售價（§7.2/§7.3）。
    """

    __tablename__ = "consignment_settlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    serialized_item_id: Mapped[int] = mapped_column(ForeignKey("serialized_items.id"), index=True)
    sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id"), index=True)
    gross: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    commission_pct: Mapped[int] = mapped_column()
    commission_amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    payout_amount: Mapped[Decimal] = mapped_column(Numeric(12, 0))
    status: Mapped[ConsignmentSettlementStatus] = mapped_column(
        Enum(ConsignmentSettlementStatus, native_enum=False, length=30, create_constraint=True),
        default=ConsignmentSettlementStatus.PENDING,
        server_default=ConsignmentSettlementStatus.PENDING.value,
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reclaim_needed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
