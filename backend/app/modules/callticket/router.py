"""叫號系統路由（docs/38）：收購前的候位清單。

**不限店長**（裁示）：排隊是日常作業，卡權限反而礙事；`KIOSK` 由 `get_current_user`
中央守衛擋掉，碰不到這裡。只做 I/O 與驗證，業務規則在 service。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.callticket.schemas import CallTicketCreateRequest, CallTicketRead
from app.modules.callticket.service import CallTicketService
from app.shared.exceptions import CallTicketNotFound

router = APIRouter(prefix="/call-tickets", tags=["call-tickets"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[CurrentUser, Depends(get_current_user)]


@router.post(
    "", response_model=CallTicketRead, status_code=status.HTTP_201_CREATED,
    operation_id="createCallTicket",
)
async def create_call_ticket(
    payload: CallTicketCreateRequest, session: SessionDep, user: AuthDep
) -> CallTicketRead:
    """登記一筆候位並配號（同店同日從 1 開始）。"""
    ticket = await CallTicketService(session).create(
        user.store_id,
        name=payload.name,
        link=payload.link,
        note=payload.note,
        actor_user_id=user.id,
    )
    await session.commit()
    await session.refresh(ticket)
    return CallTicketRead.model_validate(ticket)


@router.get("", response_model=list[CallTicketRead], operation_id="listCallTickets")
async def list_call_tickets(
    session: SessionDep,
    user: AuthDep,
    include_done: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CallTicketRead]:
    """候位清單。預設只回未完成；`include_done=true` 供事後回頭找那個表單連結。"""
    rows = await CallTicketService(session).list_tickets(
        user.store_id, include_done=include_done, limit=limit, offset=offset
    )
    return [CallTicketRead.model_validate(row) for row in rows]


@router.post(
    "/{ticket_id}/complete", response_model=CallTicketRead,
    operation_id="completeCallTicket",
)
async def complete_call_ticket(
    ticket_id: int, session: SessionDep, user: AuthDep
) -> CallTicketRead:
    """標記完成（**冪等**：已完成再按回原狀態，不報錯、不覆寫第一次的人與時間）。"""
    try:
        ticket = await CallTicketService(session).complete(
            user.store_id, ticket_id, actor_user_id=user.id
        )
    except CallTicketNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await session.commit()
    await session.refresh(ticket)
    return CallTicketRead.model_validate(ticket)
