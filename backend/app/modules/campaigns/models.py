"""campaigns 模型：門市限時促銷活動（docs/21）。

每張業務表帶 store_id（多分店就緒）。折扣 discount_pct 整數百分數 1-99。
生效窗 [starts_at, ends_at)。同店至多一個 ACTIVE（partial unique，仿 cash_session 單一 OPEN）。
只影響賣出、不影響收購。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import CampaignStatus


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class Campaign(Base, TimestampMixin):
    """門市活動。建立為 DRAFT；啟用→ACTIVE（同店至多一個）；到期/手動→ENDED；可作廢→CANCELLED。"""

    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint(
            "discount_pct >= 1 AND discount_pct <= 99", name="ck_campaigns_discount_pct"
        ),
        CheckConstraint("ends_at > starts_at", name="ck_campaigns_window"),
        # 同店至多一個生效中活動（DB 約束擋疊加，非先查再開）。
        Index(
            "uq_one_active_campaign_per_store",
            "store_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    discount_pct: Mapped[int] = mapped_column(Integer)
    applies_owned_serialized: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    applies_owned_bulk: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    applies_catalog: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    # 寄售折扣（applies_consignment=true 時）一律按比例分攤：寄售人按折後價分潤（docs/21 §8.1）。
    applies_consignment: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[CampaignStatus] = mapped_column(
        _enum_col(CampaignStatus),
        default=CampaignStatus.DRAFT,
        server_default=CampaignStatus.DRAFT.value,
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
