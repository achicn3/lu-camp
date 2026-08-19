"""叫號單 repository（docs/38）：唯一可直接碰 DB 的層。"""

from datetime import date

from sqlalchemy import func, or_, select
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
        self,
        store_id: int,
        *,
        include_done: bool,
        today: date,
        limit: int,
        offset: int,
    ) -> list[CallTicket]:
        """待處理在前（舊的先，＝排隊順序），已完成接在後面（**最近完成的先**）。

        **兩群的排序方向相反，所以分兩次查**：
        - 待處理＝排隊，先來先服務 → 日期/號碼**遞增**
        - 已完成＝回頭找先前的表單連結，要找的幾乎都是最近的 → 日期/號碼**遞減**

        用單一 `ORDER BY` 只能給一個方向：已完成若跟著遞增，歷史累積後撈到的會是
        幾百天前的單，而剛完成的那筆被擠到 limit 之外——「顯示已完成」這個開關
        時間一久就形同失效。

        **候位清單只看今天**（店主裁示 2026-08-19：「昨天未完成從今天的角度根本不重要」）。
        跨日未完成的不再出現在候位清單，但**資料不刪**——仍可由 `include_done=True`
        的歷史檢視找回它與它的表單連結。
        """
        waiting = list(
            await self._session.scalars(
                select(CallTicket)
                .where(
                    CallTicket.store_id == store_id,
                    CallTicket.status == CallTicketStatus.WAITING,
                    CallTicket.ticket_date == today,
                )
                .order_by(CallTicket.ticket_date.asc(), CallTicket.ticket_no.asc())
                .limit(limit)
                .offset(offset)
            )
        )
        if not include_done:
            return waiting
        remaining = limit - len(waiting)
        if remaining <= 0:
            return waiting
        # 歷史檢視：已完成 ＋ **跨日未完成**（後者已離開候位清單，這裡是唯一找得回的地方）
        done = await self._session.scalars(
            select(CallTicket)
            .where(
                CallTicket.store_id == store_id,
                or_(
                    CallTicket.status == CallTicketStatus.DONE,
                    CallTicket.ticket_date != today,
                ),
            )
            .order_by(CallTicket.ticket_date.desc(), CallTicket.ticket_no.desc())
            .limit(remaining)
        )
        return waiting + list(done)
