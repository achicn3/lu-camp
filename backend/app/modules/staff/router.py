"""staff 路由：員工名單。大家都能看（選待辦負責人用），增改限管理者。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.staff.schemas import StaffMemberCreate, StaffMemberRead, StaffMemberUpdate
from app.modules.staff.service import StaffService
from app.shared.enums import UserRole
from app.shared.exceptions import StaffMemberConflict, StaffMemberInvalid, StaffMemberNotFound

router = APIRouter(prefix="/staff-members", tags=["staff"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
StaffDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role(UserRole.MANAGER.value))]


@router.get("", response_model=list[StaffMemberRead], operation_id="listStaffMembers")
async def list_staff_members(
    session: SessionDep,
    user: StaffDep,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[StaffMemberRead]:
    members = await StaffService(session).list_members(
        user.store_id, include_inactive=include_inactive
    )
    return [StaffMemberRead.model_validate(m) for m in members]


@router.post(
    "",
    response_model=StaffMemberRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createStaffMember",
)
async def create_staff_member(
    payload: StaffMemberCreate, session: SessionDep, user: ManagerDep
) -> StaffMemberRead:
    try:
        member = await StaffService(session).create_member(
            user.store_id, name=payload.name, user_id=payload.user_id, actor_user_id=user.id
        )
    except StaffMemberConflict as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except StaffMemberInvalid as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await session.commit()
    return StaffMemberRead.model_validate(member)


@router.patch("/{member_id}", response_model=StaffMemberRead, operation_id="updateStaffMember")
async def update_staff_member(
    member_id: int, payload: StaffMemberUpdate, session: SessionDep, user: ManagerDep
) -> StaffMemberRead:
    try:
        member = await StaffService(session).update_member(
            user.store_id, member_id, payload, actor_user_id=user.id
        )
    except StaffMemberNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except StaffMemberConflict as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except StaffMemberInvalid as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await session.commit()
    return StaffMemberRead.model_validate(member)
