"""叫號系統模型（docs/38）：收購前的候位清單。

客人賣東西前先填表單、把連結傳給店家；店家登記後取號，處理完按「完成」。
與收購流程刻意分離（裁示「先不串」）——這裡只管排隊，不碰鑑價或庫存。
"""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import CallTicketStatus


def _enum_col(enum_cls: type) -> Enum:
    """與其他模組一致的 enum 欄位：非原生 enum ＋ CHECK 約束。

    **不可只用 String**：那樣從 DB 載回來的是 `str` 而不是 enum，
    `ticket.status is CallTicketStatus.DONE` 永遠不成立——「完成」的冪等就會失效
    （實作時真的踩到：第二次按會覆寫第一次的完成時間）。
    """
    return Enum(enum_cls, native_enum=False, length=16, create_constraint=True)


class CallTicket(Base, TimestampMixin):
    __tablename__ = "call_tickets"
    __table_args__ = (
        # 同店同日的號碼唯一——並發配號撞號時的最後防線（service 會重取號再寫）。
        Index("uq_call_tickets_store_date_no", "store_id", "ticket_date", "ticket_no", unique=True),
        # 待處理清單的查詢路徑：本店 × 狀態。
        Index("ix_call_tickets_store_status", "store_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    # **台北營業日**（core.time.store_date），每日重置的依據。
    # 不可用 UTC 日期：台灣時間 08:00 前的 UTC 還是前一天，早上的號碼會接續昨天。
    ticket_date: Mapped[date] = mapped_column(Date)
    ticket_no: Mapped[int] = mapped_column()
    name: Mapped[str] = mapped_column(String(60))
    # 客人填的表單連結；只收 http/https（schema 層驗），空字串正規化為 NULL。
    link: Mapped[str | None] = mapped_column(String(500))
    note: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[CallTicketStatus] = mapped_column(
        _enum_col(CallTicketStatus),
        default=CallTicketStatus.WAITING,
        server_default=CallTicketStatus.WAITING.value,
    )
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    completed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
