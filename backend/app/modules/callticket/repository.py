"""叫號單 repository（docs/38）：唯一可直接碰 DB 的層。"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.callticket.models import CallTicket
from app.shared.enums import CallTicketStatus


class CallTicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def next_ticket_no(self, store_id: int, ticket_date: date) -> int:
        """同店同日的下一個號碼（從 1 開始）。"""
        current = await self._session.scalar(
            select(func.max(CallTicket.ticket_no)).where(
                CallTicket.store_id == store_id, CallTicket.ticket_date == ticket_date
            )
        )
        return int(current or 0) + 1

    def add(self, ticket: CallTicket) -> None:
        self._session.add(ticket)

    async def get(self, store_id: int, ticket_id: int) -> CallTicket | None:
        ticket: CallTicket | None = await self._session.scalar(
            select(CallTicket).where(
                CallTicket.id == ticket_id, CallTicket.store_id == store_id
            )
        )
        return ticket

    async def list_tickets(
        self, store_id: int, *, include_done: bool, limit: int, offset: int
    ) -> list[CallTicket]:
        """待處理在前（號碼小的先），已完成的接在後面（最近完成的先）。

        **跨日未完成的仍列出**——那是客人真的還在等的單，不得因為換日就消失。
        """
        stmt = select(CallTicket).where(CallTicket.store_id == store_id)
        if not include_done:
            stmt = stmt.where(CallTicket.status == CallTicketStatus.WAITING)
        stmt = stmt.order_by(
            CallTicket.status.desc(),  # WAITING > DONE（字母序反向）
            CallTicket.ticket_date.asc(),
            CallTicket.ticket_no.asc(),
        )
        rows = await self._session.scalars(stmt.limit(limit).offset(offset))
        return list(rows)
