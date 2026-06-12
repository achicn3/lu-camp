"""settings 模型：每店單列、具型別的系統設定（docs/01 P、docs/03）。

每店至多一列（store_id 唯一）。值的預設集中於 defaults.py；此處 server_default 與其一致，
供直接 DB insert 時亦有合理預設。金額相關為比率/百分數，非金額本身。
"""

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin


class StoreSettings(Base, TimestampMixin):
    """單店系統設定。每店一列（store_id 唯一）。"""

    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("store_id", name="uq_settings_store_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    einvoice_enabled: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    tax_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), server_default=text("0.05"), nullable=False
    )
    default_commission_pct: Mapped[int] = mapped_column(
        Integer, server_default=text("50"), nullable=False
    )
    default_margin_pct: Mapped[int] = mapped_column(
        Integer, server_default=text("45"), nullable=False
    )
    # 購物金溢價率（docs/16 §1.5；SC-5 將補 min/max 與建議值引擎，本欄為 SC-2 所需最小前移）
    premium_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), server_default=text("0.10"), nullable=False
    )
