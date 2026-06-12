"""acquisition 模型：收購/寄售入庫單（單頭）。

入庫明細落在 inventory 的 serialized_items / bulk_lots（其 acquisition_id 外鍵回此）；
付現則記在 cashdrawer 的 cash_movements。本表只記單頭與付現總額。
金額用 NUMERIC(scale 0) → Decimal（NT$ 整數元）。
"""

from decimal import Decimal

from sqlalchemy import Enum, ForeignKey, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import AcquisitionType, PayoutMethod


class Acquisition(Base, TimestampMixin):
    """收購/寄售入庫單。created_at 即收購日期；id 即收購單號。"""

    __tablename__ = "acquisitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    type: Mapped[AcquisitionType] = mapped_column(
        Enum(AcquisitionType, native_enum=False, length=30, create_constraint=True)
    )
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    clerk_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # 付現總額（BUYOUT/BULK_LOT 用；CONSIGNMENT 不付現為 NULL）。
    total_cash_paid: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    # 撥款方式與拆分（SC-2，docs/16 §1.7）：現金部分走錢櫃、購物金部分入帳本；
    # 既有/CONSIGNMENT 資料為 CASH 預設。
    payout_method: Mapped[PayoutMethod] = mapped_column(
        Enum(PayoutMethod, native_enum=False, length=20, create_constraint=True),
        default=PayoutMethod.CASH,
        server_default=text("'CASH'"),
    )
    payout_cash_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    payout_credit_cash_equivalent: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    note: Mapped[str | None] = mapped_column(String(500))
