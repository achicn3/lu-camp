"""campaigns 模型：門市限時促銷活動（docs/21）。

每張業務表帶 store_id（多分店就緒）。折扣 discount_pct 整數百分數 1-99。
生效窗 [starts_at, ends_at)。只影響賣出、不影響收購。
v2（docs/40，2026-09-23）：同店可多個 ACTIVE；stackable 決定能否與其他活動併用；
範圍可細到分類／品牌／型號／單件／一般商品／販售籃（campaign_targets，包含或排除）。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import CampaignStatus, CampaignTargetMode, CampaignTargetType


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class Campaign(Base, TimestampMixin):
    """門市活動。建立為 DRAFT；啟用→ACTIVE（可同時多個）；到期/手動→ENDED；可作廢→CANCELLED。"""

    __tablename__ = "campaigns"
    __table_args__ = (
        CheckConstraint(
            "discount_pct >= 1 AND discount_pct <= 99", name="ck_campaigns_discount_pct"
        ),
        CheckConstraint("ends_at > starts_at", name="ck_campaigns_window"),
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
    # 可與其他活動疊加（連乘）；false＝不跟任何活動併用，由定價挑對客人最划算的（docs/40）。
    stackable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))


class CampaignTarget(Base, TimestampMixin):
    """活動範圍條件：包含或排除某個分類／品牌／型號／單件／一般商品／販售籃（docs/40 §3）。

    target_id 依 target_type 指向不同表，故不設 FK；建立時由 service 驗證屬本店。
    """

    __tablename__ = "campaign_targets"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "mode", "target_type", "target_id", name="uq_campaign_targets_entry"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    mode: Mapped[CampaignTargetMode] = mapped_column(_enum_col(CampaignTargetMode))
    target_type: Mapped[CampaignTargetType] = mapped_column(_enum_col(CampaignTargetType))
    target_id: Mapped[int] = mapped_column(Integer)
