"""贈品／折扣原因代碼的讀取與管理路由。

POS 的贈品與折扣對話框需要選單（任何登入者可讀，只回啟用中的）；
新增與修改屬後台，限 MANAGER。**停用不實刪**：歷史單據引用過的原因不能因為後台刪掉就消失
（單據另存名稱快照），停用只是讓它不再出現在選單。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.sales.schemas import ReasonCreateRequest, ReasonRead, ReasonUpdateRequest
from app.modules.sales.service import SalesService
from app.shared.enums import UserRole
from app.shared.exceptions import ReasonConflict, ReasonNotFound

router = APIRouter(tags=["sales"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role(UserRole.MANAGER.value))]
IncludeInactive = Annotated[
    bool, Query(description="連停用的原因一起列出（管理頁用；POS 選單不需要）")
]


def _read(reason: object) -> ReasonRead:
    return ReasonRead.model_validate(reason, from_attributes=True)


def _not_found(exc: ReasonNotFound) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


def _conflict(exc: ReasonConflict) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/gift-reasons", response_model=list[ReasonRead], operation_id="listGiftReasons")
async def list_gift_reasons(
    session: SessionDep, user: CurrentUserDep, include_inactive: IncludeInactive = False
) -> list[ReasonRead]:
    reasons = await SalesService(session).list_gift_reasons(
        user.store_id, include_inactive=include_inactive
    )
    return [_read(reason) for reason in reasons]


@router.post(
    "/gift-reasons",
    response_model=ReasonRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createGiftReason",
)
async def create_gift_reason(
    body: ReasonCreateRequest, session: SessionDep, user: ManagerDep
) -> ReasonRead:
    try:
        reason = await SalesService(session).create_gift_reason(
            user.store_id,
            code=body.code,
            name=body.name,
            requires_note=body.requires_note,
            sort_order=body.sort_order,
            actor_user_id=user.id,
        )
    except ReasonConflict as exc:
        raise _conflict(exc) from exc
    await session.commit()
    return _read(reason)


@router.patch(
    "/gift-reasons/{reason_id}", response_model=ReasonRead, operation_id="updateGiftReason"
)
async def update_gift_reason(
    reason_id: int, body: ReasonUpdateRequest, session: SessionDep, user: ManagerDep
) -> ReasonRead:
    try:
        reason = await SalesService(session).update_gift_reason(
            user.store_id,
            reason_id,
            actor_user_id=user.id,
            name=body.name,
            requires_note=body.requires_note,
            sort_order=body.sort_order,
            is_active=body.is_active,
        )
    except ReasonNotFound as exc:
        raise _not_found(exc) from exc
    await session.commit()
    return _read(reason)


@router.get(
    "/discount-reasons", response_model=list[ReasonRead], operation_id="listDiscountReasons"
)
async def list_discount_reasons(
    session: SessionDep, user: CurrentUserDep, include_inactive: IncludeInactive = False
) -> list[ReasonRead]:
    reasons = await SalesService(session).list_discount_reasons(
        user.store_id, include_inactive=include_inactive
    )
    return [_read(reason) for reason in reasons]


@router.post(
    "/discount-reasons",
    response_model=ReasonRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createDiscountReason",
)
async def create_discount_reason(
    body: ReasonCreateRequest, session: SessionDep, user: ManagerDep
) -> ReasonRead:
    try:
        reason = await SalesService(session).create_discount_reason(
            user.store_id,
            code=body.code,
            name=body.name,
            requires_note=body.requires_note,
            sort_order=body.sort_order,
            actor_user_id=user.id,
        )
    except ReasonConflict as exc:
        raise _conflict(exc) from exc
    await session.commit()
    return _read(reason)


@router.patch(
    "/discount-reasons/{reason_id}",
    response_model=ReasonRead,
    operation_id="updateDiscountReason",
)
async def update_discount_reason(
    reason_id: int, body: ReasonUpdateRequest, session: SessionDep, user: ManagerDep
) -> ReasonRead:
    try:
        reason = await SalesService(session).update_discount_reason(
            user.store_id,
            reason_id,
            actor_user_id=user.id,
            name=body.name,
            requires_note=body.requires_note,
            sort_order=body.sort_order,
            is_active=body.is_active,
        )
    except ReasonNotFound as exc:
        raise _not_found(exc) from exc
    await session.commit()
    return _read(reason)
