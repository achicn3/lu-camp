"""consignment 路由（Phase 4 / 4A）：寄售結算查詢與付款給寄售人。

整筆原子性：service 只 flush；router 成功才 commit、任何失敗先 rollback 再回錯，確保
「現金出帳了但結算沒轉 PAID」之類的半套不落地（給現金永遠在系統成功之後）。
付款限店員/管理者（CurrentUserDep）+ 稽核；現金出帳須開帳中（invariant #8）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.consignment.schemas import ConsignmentSettlementRead
from app.modules.consignment.service import ConsignmentService
from app.shared.enums import ConsignmentSettlementStatus
from app.shared.exceptions import (
    DomainError,
    IdempotencyKeyConflict,
    NoOpenCashSession,
    SettlementNotFound,
    SettlementNotPending,
)

router = APIRouter(prefix="/consignment", tags=["consignment"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]

_STATUS_BY_EXC: dict[type[DomainError], int] = {
    IdempotencyKeyConflict: status.HTTP_409_CONFLICT,
    SettlementNotFound: status.HTTP_404_NOT_FOUND,
    SettlementNotPending: status.HTTP_409_CONFLICT,
    NoOpenCashSession: status.HTTP_409_CONFLICT,
}


@router.get(
    "/settlements",
    response_model=list[ConsignmentSettlementRead],
    operation_id="listConsignmentSettlements",
)
async def list_settlements(
    session: SessionDep,
    user: CurrentUserDep,
    settlement_status: Annotated[ConsignmentSettlementStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ConsignmentSettlementRead]:
    """店內寄售結算列（可篩 status，新到舊、分頁；§4 店別範圍）。"""
    rows = await ConsignmentService(session).list_settlements(
        user.store_id, status=settlement_status, limit=limit, offset=offset
    )
    return [ConsignmentSettlementRead.model_validate(row) for row in rows]


@router.post(
    "/settlements/{settlement_id}/pay",
    response_model=ConsignmentSettlementRead,
    operation_id="payConsignmentSettlement",
)
async def pay_settlement(
    settlement_id: int,
    session: SessionDep,
    user: CurrentUserDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=80)],
) -> ConsignmentSettlementRead:
    """付款給寄售人（payout = 售價 − 抽成）：現金出帳並結算轉 PAID，全程稽核、整筆原子。

    需開帳中（invariant #8）→ 否則 409；已付/已取消 → 409；找不到/他店 → 404。
    併發/重送以結算列鎖為準，只一筆成功、不重複出帳。
    """
    svc = ConsignmentService(session)
    try:
        settlement = await svc.pay_settlement(
            user.store_id,
            settlement_id,
            actor_user_id=user.id,
            idempotency_key=idempotency_key,
        )
    except DomainError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=_STATUS_BY_EXC.get(type(exc), status.HTTP_400_BAD_REQUEST),
            detail=str(exc),
        ) from exc
    except Exception:
        await session.rollback()
        raise
    await session.commit()
    return ConsignmentSettlementRead.model_validate(settlement)
