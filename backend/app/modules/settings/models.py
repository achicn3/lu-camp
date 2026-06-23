"""settings 模型：每店單列、具型別的系統設定（docs/01 P、docs/03）。

每店至多一列（store_id 唯一）。值的預設集中於 defaults.py；此處 server_default 與其一致，
供直接 DB insert 時亦有合理預設。金額相關為比率/百分數，非金額本身。
"""

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.modules.settings.defaults import DEFAULT_STORE_CREDIT_ENGINE_PARAMS


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
    allow_clerk_manage_categories: Mapped[bool] = mapped_column(
        Boolean, server_default=text("false"), nullable=False
    )
    # 購物金溢價率與政策界線（docs/16 §1.5/§6.1）：premium_rate 夾在 [min, max]。
    premium_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), server_default=text("0.10"), nullable=False
    )
    premium_rate_min: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), server_default=text("0.0000"), nullable=False
    )
    premium_rate_max: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), server_default=text("0.2000"), nullable=False
    )
    # 月固定現金支出（整數元）：負債健康比分母（docs/16 §5A）；手動維護，預設 0（=N/A）。
    monthly_fixed_cash_outflow: Mapped[Decimal] = mapped_column(
        Numeric(12, 0), server_default=text("0"), nullable=False
    )
    # 購物金低消門檻（整數元）：非餐飲消費（total − 餐飲）未達此值則不可折抵購物金。預設 0＝不限制。
    store_credit_min_spend: Mapped[Decimal] = mapped_column(
        Numeric(12, 0), server_default=text("0"), nullable=False
    )
    # 建議值引擎可調參數（docs/16 §1.5/§6；SC-5b 引擎使用）。server_default 與 defaults 一致。
    store_credit_engine_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(f"'{json.dumps(DEFAULT_STORE_CREDIT_ENGINE_PARAMS)}'::jsonb"),
    )


class PremiumRateHistory(Base):
    """購物金溢價率變更留痕（docs/16 §1.3）：僅 INSERT；每次 premium_rate 變更寫一列。"""

    __tablename__ = "premium_rate_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    old_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    new_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    # 變更當下的系統建議值（SC-5b 引擎；冷啟動/未算時為 NULL）。
    suggested_rate_at_change: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    reason: Mapped[str | None] = mapped_column(String(200))
