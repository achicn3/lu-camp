"""campaigns 模型：門市限時促銷活動（docs/21）。

每張業務表帶 store_id（多分店就緒）。折扣 discount_pct 整數百分數 1-99。
生效窗 [starts_at, ends_at)。只影響賣出、不影響收購。
v2（docs/40，2026-09-23）：同店可多個 ACTIVE；stackable 決定能否與其他活動併用；
範圍可細到分類／品牌／型號／單件／一般商品／販售籃（campaign_targets，包含或排除）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import CampaignKind, CampaignStatus, CampaignTargetMode, CampaignTargetType


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class Campaign(Base, TimestampMixin):
    """門市活動。建立為 DRAFT；啟用→ACTIVE（可同時多個）；到期/手動→ENDED；可作廢→CANCELLED。"""

    __tablename__ = "campaigns"
    __table_args__ = (
        # 類型與數值一致（docs/40 P2、P3）：打折只填 discount_pct，特價只填 fixed_price，
        # 折金額只填 amount_off，買 N 送 M 只填 buy_qty／free_qty，組合價只填 bundle_price；
        # 後兩者不可開寄售（裁示 7）。
        CheckConstraint(
            "(kind = 'PERCENT_OFF' AND discount_pct BETWEEN 1 AND 99"
            " AND fixed_price IS NULL AND amount_off IS NULL"
            " AND buy_qty IS NULL AND free_qty IS NULL AND bundle_price IS NULL)"
            " OR (kind = 'FIXED_PRICE' AND fixed_price > 0"
            " AND discount_pct IS NULL AND amount_off IS NULL"
            " AND buy_qty IS NULL AND free_qty IS NULL AND bundle_price IS NULL)"
            " OR (kind = 'AMOUNT_OFF' AND amount_off > 0"
            " AND discount_pct IS NULL AND fixed_price IS NULL"
            " AND buy_qty IS NULL AND free_qty IS NULL AND bundle_price IS NULL)"
            " OR (kind = 'BUY_N_GET_M' AND buy_qty BETWEEN 1 AND 99 AND free_qty BETWEEN 1 AND 99"
            " AND discount_pct IS NULL AND fixed_price IS NULL AND amount_off IS NULL"
            " AND bundle_price IS NULL AND NOT applies_consignment)"
            " OR (kind = 'BUNDLE' AND bundle_price > 0"
            " AND discount_pct IS NULL AND fixed_price IS NULL AND amount_off IS NULL"
            " AND buy_qty IS NULL AND free_qty IS NULL AND NOT applies_consignment)",
            name="ck_campaigns_kind_value",
        ),
        CheckConstraint("ends_at > starts_at", name="ck_campaigns_window"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[CampaignKind] = mapped_column(
        _enum_col(CampaignKind),
        default=CampaignKind.PERCENT_OFF,
        server_default=CampaignKind.PERCENT_OFF.value,
    )
    # 打折（kind=PERCENT_OFF）才有；特價／折金額為 None。
    discount_pct: Mapped[int | None] = mapped_column(Integer)
    # 指定特價／每件折金額（含稅整數元）；只有對應類型才有值。
    fixed_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    amount_off: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
    # 買 N 送 M（kind=BUY_N_GET_M）才有：買 buy_qty 件送 free_qty 件。
    buy_qty: Mapped[int | None] = mapped_column(Integer)
    free_qty: Mapped[int | None] = mapped_column(Integer)
    # 組合價（kind=BUNDLE）才有：湊齊各格子的整組含稅價；格子在 campaign_bundle_slots。
    bundle_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 0))
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


class CampaignBundleSlot(Base, TimestampMixin):
    """組合價的一個格子（docs/40 §4）：符合任一範圍條件的商品湊 qty 件。"""

    __tablename__ = "campaign_bundle_slots"
    __table_args__ = (
        UniqueConstraint("campaign_id", "slot_no", name="uq_campaign_bundle_slots_no"),
        CheckConstraint("qty BETWEEN 1 AND 99", name="ck_campaign_bundle_slots_qty"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    slot_no: Mapped[int] = mapped_column(Integer)
    qty: Mapped[int] = mapped_column(Integer)


class CampaignBundleSlotTarget(Base, TimestampMixin):
    """格子的範圍條件（只有「包含」）。target_id 依類型指向不同表，由 service 驗證屬本店。"""

    __tablename__ = "campaign_bundle_slot_targets"
    __table_args__ = (
        UniqueConstraint(
            "slot_id", "target_type", "target_id", name="uq_bundle_slot_targets_entry"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("campaign_bundle_slots.id"), index=True)
    target_type: Mapped[CampaignTargetType] = mapped_column(_enum_col(CampaignTargetType))
    target_id: Mapped[int] = mapped_column(Integer)
