"""stocktake 路由：建盤點單、查詢、確認調整。整筆原子：service 只 flush，router 成功才 commit。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.stocktake.schemas import StocktakeConfirmRequest, StocktakeRead
from app.modules.stocktake.service import StocktakeService
from app.shared.exceptions import (
    CrossStoreReference,
    DomainError,
    OwnershipValidationError,
    StocktakeLineInvalid,
    StocktakeNotDraft,
    StocktakeNotFound,
)

router = APIRouter(tags=["stocktake"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]

_STATUS_BY_EXC: dict[type[DomainError], int] = {
    StocktakeNotFound: status.HTTP_404_NOT_FOUND,
    StocktakeNotDraft: status.HTTP_409_CONFLICT,
    StocktakeLineInvalid: status.HTTP_422_UNPROCESSABLE_CONTENT,
    CrossStoreReference: status.HTTP_422_UNPROCESSABLE_CONTENT,
    OwnershipValidationError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def _map_domain_error(exc: DomainError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_BY_EXC.get(type(exc), status.HTTP_400_BAD_REQUEST),
        detail=str(exc),
    )


@router.post(
    "/stocktakes",
    response_model=StocktakeRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createStocktake",
)
async def create_stocktake(session: SessionDep, user: CurrentUserDep) -> StocktakeRead:
    """建立盤點單並快照店內所有數量型商品的 system_qty。"""
    stocktake = await StocktakeService(session).create_stocktake(
        user.store_id, actor_user_id=user.id
    )
    await session.commit()
    return StocktakeRead.from_model(stocktake)


@router.get("/stocktakes", response_model=list[StocktakeRead], operation_id="listStocktakes")
async def list_stocktakes(
    session: SessionDep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[StocktakeRead]:
    stocktakes = await StocktakeService(session).list_stocktakes(
        user.store_id, limit=limit, offset=offset
    )
    return [StocktakeRead.from_model(s) for s in stocktakes]


@router.get("/stocktakes/{stocktake_id}", response_model=StocktakeRead, operation_id="getStocktake")
async def get_stocktake(
    stocktake_id: int, session: SessionDep, user: CurrentUserDep
) -> StocktakeRead:
    stocktake = await StocktakeService(session).get_stocktake(user.store_id, stocktake_id)
    if stocktake is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到盤點單")
    return StocktakeRead.from_model(stocktake)


@router.post(
    "/stocktakes/{stocktake_id}/confirm",
    response_model=StocktakeRead,
    operation_id="confirmStocktake",
)
async def confirm_stocktake(
    stocktake_id: int,
    payload: StocktakeConfirmRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> StocktakeRead:
    """確認盤點：依實點數即時校正現量並寫 ADJUST 帳；DRAFT→CONFIRMED（僅一次）。"""
    counts = {count.catalog_product_id: count.counted_qty for count in payload.counts}
    svc = StocktakeService(session)
    try:
        stocktake = await svc.confirm_stocktake(
            user.store_id, stocktake_id, counts, actor_user_id=user.id
        )
    except DomainError as exc:
        await session.rollback()
        raise _map_domain_error(exc) from exc
    await session.commit()
    return StocktakeRead.from_model(stocktake)
