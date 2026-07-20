"""cashdrawer 路由：開帳/結帳/現金異動。I/O 與權限，業務邏輯委派 service。

寫入端點在成功後 commit、領域錯誤時 rollback 並回可辨識的 HTTP 錯誤。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.cashdrawer.schemas import (
    CashMovementCreateRequest,
    CashMovementRead,
    CashSessionCloseRequest,
    CashSessionOpenRequest,
    CashSessionRead,
)
from app.modules.cashdrawer.service import CashDrawerService
from app.shared.enums import CashMovementType
from app.shared.exceptions import (
    CashSessionAlreadyClosed,
    CashSessionAlreadyOpen,
    NoOpenCashSession,
)

router = APIRouter(prefix="/cash-sessions", tags=["cashdrawer"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role("MANAGER"))]


@router.post(
    "/open",
    response_model=CashSessionRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="openCashSession",
)
async def open_cash_session(
    payload: CashSessionOpenRequest, session: SessionDep, user: CurrentUserDep
) -> CashSessionRead:
    svc = CashDrawerService(session)
    try:
        cs = await svc.open_session(user.store_id, user.id, payload.opening_float)
    except CashSessionAlreadyOpen as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return CashSessionRead.from_model(cs)


@router.get("/current", response_model=CashSessionRead | None, operation_id="getCurrentCashSession")
async def get_current_cash_session(
    session: SessionDep, user: CurrentUserDep
) -> CashSessionRead | None:
    cs = await CashDrawerService(session).get_current_session(user.store_id)
    return CashSessionRead.from_model(cs) if cs is not None else None


@router.get(
    "/{session_id}/movements",
    response_model=list[CashMovementRead],
    operation_id="listCashMovements",
)
async def list_cash_movements(
    session_id: int,
    session: SessionDep,
    user: CurrentUserDep,
) -> list[CashMovementRead]:
    """列出本店指定現金班別的異動，最新一筆在前。"""
    svc = CashDrawerService(session)
    cash_session = await svc.get_session(user.store_id, session_id)
    if cash_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到現金班別")
    movements = await svc.list_session_movements(cash_session)
    return [CashMovementRead.from_model(movement) for movement in movements]


@router.post(
    "/{session_id}/movements",
    response_model=CashMovementRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="recordCashMovement",
)
async def record_cash_movement(
    session_id: int,
    payload: CashMovementCreateRequest,
    session: SessionDep,
    user: ManagerDep,
) -> CashMovementRead:
    """手動現金調整（限 MANAGER；docs/10 §4）。

    端點**僅接受 MANUAL_ADJUST**：SALE_IN/BUYOUT_OUT/CONSIGNMENT_PAYOUT_OUT 為
    系統內部流程產生的營業現金流，開放 API 灌入等同允許捏造現金帳（Codex P1）。
    事由必填並隨 audit_log 留痕（CLAUDE.md §5）。
    """
    if payload.type != CashMovementType.MANUAL_ADJUST:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="此端點僅接受 MANUAL_ADJUST；系統類型由內部流程產生",
        )
    svc = CashDrawerService(session)
    current = await svc.get_current_session(user.store_id)
    if current is None or current.id != session_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="此 session 非開帳中或不存在"
        )
    movement = await svc.record_movement(
        user.store_id,
        payload.type,
        payload.amount,
        actor_user_id=user.id,
        ref_type="manual",
        note=payload.note,
    )
    await session.commit()
    return CashMovementRead.from_model(movement)


@router.post(
    "/{session_id}/close",
    response_model=CashSessionRead,
    operation_id="closeCashSession",
)
async def close_cash_session(
    session_id: int,
    payload: CashSessionCloseRequest,
    session: SessionDep,
    user: CurrentUserDep,
) -> CashSessionRead:
    svc = CashDrawerService(session)
    cs = await svc.get_session(user.store_id, session_id)
    if cs is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到現金班別")
    try:
        closed = await svc.close_session(cs, payload.counted_amount, user.id)
    except (CashSessionAlreadyClosed, NoOpenCashSession) as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return CashSessionRead.from_model(closed)
