"""stocktake 模型：盤點單與盤點明細。

第一版只盤數量型商品（catalog_products）：建單時為每個 catalog 商品快照 system_qty；確認時
依實點數即時校正現量並寫 ADJUST(STOCKTAKE) 帳。序號品/散裝品的盤點為後續切片。
"""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, TimestampMixin
from app.shared.enums import StocktakeStatus


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=30, create_constraint=True)


class Stocktake(Base, TimestampMixin):
    """盤點單。建立即 DRAFT（已快照 system_qty）；確認調整後轉 CONFIRMED（僅一次）。"""

    __tablename__ = "stocktakes"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    status: Mapped[StocktakeStatus] = mapped_column(
        _enum_col(StocktakeStatus),
        default=StocktakeStatus.DRAFT,
        server_default=StocktakeStatus.DRAFT.value,
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    lines: Mapped[list["StocktakeLine"]] = relationship(
        back_populates="stocktake",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="StocktakeLine.id",
    )


class StocktakeLine(Base):
    """盤點明細。system_qty＝建單時快照、counted_qty＝確認時的實點數（未點為 NULL）。"""

    __tablename__ = "stocktake_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    stocktake_id: Mapped[int] = mapped_column(
        ForeignKey("stocktakes.id", ondelete="CASCADE"), index=True
    )
    catalog_product_id: Mapped[int] = mapped_column(ForeignKey("catalog_products.id"), index=True)
    system_qty: Mapped[int] = mapped_column(Integer)
    counted_qty: Mapped[int | None] = mapped_column(Integer)

    stocktake: Mapped[Stocktake] = relationship(back_populates="lines")

    @property
    def variance(self) -> int | None:
        """實點 − 快照（盤盈為正、盤虧為負）；未點回 None。供報表/前端顯示差異。"""
        return None if self.counted_qty is None else self.counted_qty - self.system_qty
