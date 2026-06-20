"""returns 路由：建立退貨與查詢退貨單。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.returns.schemas import ReturnCreateRequest, ReturnRead
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.shared.exceptions import (
    DomainError,
    IdempotencyKeyConflict,
    NoOpenCashSession,
    ReturnConflict,
    ReturnLineInvalid,
    ReturnNotFound,
    ReturnSaleNotFound,
)

router = APIRouter(tags=["returns"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]

_STATUS_BY_EXC: dict[type[DomainError], int] = {
    ReturnNotFound: status.HTTP_404_NOT_FOUND,
    ReturnSaleNotFound: status.HTTP_404_NOT_FOUND,
    ReturnLineInvalid: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ReturnConflict: status.HTTP_409_CONFLICT,
    NoOpenCashSession: status.HTTP_409_CONFLICT,
    IdempotencyKeyConflict: status.HTTP_409_CONFLICT,
}


def _map_domain_error(exc: DomainError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_BY_EXC.get(type(exc), status.HTTP_400_BAD_REQUEST),
        detail=str(exc),
    )


@router.post(
    "/returns",
    response_model=ReturnRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createReturn",
)
async def create_return(
    payload: ReturnCreateRequest,
    session: SessionDep,
    user: CurrentUserDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=80)],
) -> ReturnRead:
    svc = ReturnsService(session)
    inputs = [ReturnLineInput(line.sale_line_id, line.qty) for line in payload.lines]
    requested = {line.sale_line_id: line.qty for line in payload.lines}
    try:
        customer_return = await svc.create_return(
            user.store_id,
            sale_id=payload.sale_id,
            lines=inputs,
            reason=payload.reason,
            actor_user_id=user.id,
            idempotency_key=idempotency_key,
        )
    except IntegrityError as exc:
        await session.rollback()
        # 僅處理 idempotency 唯一約束違反（並行重送）；其他完整性錯誤不吞、照常往外拋。
        if "uq_returns_store_idempotency_key" not in str(exc.orig):
            raise
        try:
            existing = await svc.find_idempotent_replay(
                user.store_id,
                idempotency_key,
                sale_id=payload.sale_id,
                requested=requested,
                reason=payload.reason.strip(),
            )
        except IdempotencyKeyConflict as conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(conflict)
            ) from conflict
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="退貨衝突，請重試"
            ) from exc
        return ReturnRead.from_model(existing)
    except DomainError as exc:
        await session.rollback()
        raise _map_domain_error(exc) from exc
    await session.commit()
    return ReturnRead.from_model(customer_return)


@router.get("/returns/{return_id}", response_model=ReturnRead, operation_id="getReturn")
async def get_return(return_id: int, session: SessionDep, user: CurrentUserDep) -> ReturnRead:
    customer_return = await ReturnsService(session).get_return(user.store_id, return_id)
    if customer_return is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到退貨單")
    return ReturnRead.from_model(customer_return)
