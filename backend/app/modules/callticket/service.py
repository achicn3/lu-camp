"""叫號單 service（docs/38）：配號、完成、查詢。所有業務規則集中於此。"""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import store_date, utc_now
from app.modules.callticket.models import CallTicket
from app.modules.callticket.repository import CallTicketRepository
from app.shared.enums import CallTicketStatus
from app.shared.exceptions import CallTicketNotFound

# 單店單機的並發極低，但唯一索引仍可能撞號（兩個請求同時讀到同一個 max）。
# 撞了就重取號再寫一次；重試上限刻意很小——超過就是異常，不該無限迴圈。
_ALLOCATION_RETRIES = 3
# PostgreSQL unique_violation
_UNIQUE_VIOLATION_SQLSTATE = "23505"


def _is_unique_violation(exc: IntegrityError) -> bool:
    """僅辨識唯一鍵衝突；外鍵/非空等其他違反不得被當成撞號重試。"""
    return getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION_SQLSTATE


class CallTicketService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = CallTicketRepository(session)

    async def create(
        self,
        store_id: int,
        *,
        name: str,
        link: str | None = None,
        note: str | None = None,
        actor_user_id: int,
        now: datetime | None = None,
    ) -> CallTicket:
        """登記一筆並配號（同店同日從 1 開始遞增）。

        `now` 僅供測試注入固定時點；正式路徑一律用 `utc_now()`。
        """
        moment = now if now is not None else utc_now()
        ticket_date = store_date(moment)
        for attempt in range(_ALLOCATION_RETRIES):
            ticket = CallTicket(
                store_id=store_id,
                ticket_date=ticket_date,
                ticket_no=await self._repo.next_ticket_no(store_id, ticket_date),
                name=name,
                link=link,
                note=note,
                status=CallTicketStatus.WAITING,
                created_by_user_id=actor_user_id,
            )
            try:
                # **savepoint**：撞號只退掉這一次的新增，不動呼叫端已完成的工作
                # （用 session.rollback() 會把整個交易一起回滾）。
                async with self._session.begin_nested():
                    self._repo.add(ticket)
                    await self._session.flush()
            except IntegrityError as exc:
                # **只重試唯一鍵衝突**：其他 IntegrityError（例如 store_id/使用者外鍵不存在）
                # 全部原樣拋出——把它們當成撞號去重試，只會把真正的錯誤重試三次再拋，
                # 或更糟：吞成看似成功。
                if not _is_unique_violation(exc) or attempt == _ALLOCATION_RETRIES - 1:
                    raise
                continue
            return ticket
        raise AssertionError("unreachable")  # pragma: no cover

    async def complete(
        self, store_id: int, ticket_id: int, *, actor_user_id: int
    ) -> CallTicket:
        """標記完成。**冪等**：已完成再按回原狀態，不報錯、不覆寫第一次的人與時間。

        手滑連按兩下或兩台裝置同時按都不該有人看到錯誤；而「誰先完成的」是事實，
        後來者不得蓋掉。
        """
        ticket = await self._repo.get(store_id, ticket_id)
        if ticket is None:
            raise CallTicketNotFound(f"叫號單不存在或不屬於本店：id={ticket_id}")
        if ticket.status is CallTicketStatus.DONE:
            return ticket
        ticket.status = CallTicketStatus.DONE
        ticket.completed_by_user_id = actor_user_id
        ticket.completed_at = datetime.now(UTC)
        await self._session.flush()
        return ticket

    async def list_tickets(
        self,
        store_id: int,
        *,
        include_done: bool = False,
        limit: int = 100,
        offset: int = 0,
        now: datetime | None = None,
    ) -> list[CallTicket]:
        """預設只回**今天**的待處理；`include_done=True` 供事後回頭找那個表單連結。"""
        moment = now if now is not None else utc_now()
        return await self._repo.list_tickets(
            store_id,
            include_done=include_done,
            today=store_date(moment),
            limit=limit,
            offset=offset,
        )
