"""openingcheck 路由：今天的檢查狀態、打勾／略過、自訂項目增刪。

打勾與略過是店員日常操作（一般權限）；增刪自訂項目屬設定，限 MANAGER。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.openingcheck.schemas import (
    OpeningCheckItemCreateRequest,
    OpeningCheckItemDoneRequest,
    OpeningCheckItemRead,
    OpeningCheckSkipRequest,
    OpeningCheckTodayRead,
)
from app.modules.openingcheck.service import OpeningCheckService
from app.shared.enums import UserRole
from app.shared.exceptions import OpeningCheckConflict

router = APIRouter(prefix="/opening-check", tags=["opening-check"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
StaffDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role(UserRole.MANAGER.value))]


@router.get("/today", response_model=OpeningCheckTodayRead, operation_id="getOpeningCheckToday")
async def get_today(session: SessionDep, user: StaffDep) -> OpeningCheckTodayRead:
    """今天做到哪。裝置狀態不在這裡——前端直接問 hardware-agent。"""
    return await OpeningCheckService(session).today(user.store_id)  # 唯讀，不寫庫


@router.post(
    "/today/items/{item_id}",
    response_model=OpeningCheckTodayRead,
    operation_id="setOpeningCheckItemDone",
)
async def set_item_done(
    item_id: int,
    payload: OpeningCheckItemDoneRequest,
    session: SessionDep,
    user: StaffDep,
) -> OpeningCheckTodayRead:
    """勾／取消勾一條確認事項（按錯了要能取消）。"""
    try:
        result = await OpeningCheckService(session).set_item_done(
            user.store_id, item_id, done=payload.done
        )
    except OpeningCheckConflict as exc:  # 當天第一筆兩台同時進來
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if result is None:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到檢查項目")
    await session.commit()
    return result


@router.post("/today/skip", response_model=OpeningCheckTodayRead, operation_id="skipOpeningCheck")
async def skip(
    payload: OpeningCheckSkipRequest, session: SessionDep, user: StaffDep
) -> OpeningCheckTodayRead:
    """今天略過一個自動項目（裁示：不必填原因）；明天會再檢查一次。"""
    try:
        result = await OpeningCheckService(session).skip(
            user.store_id, payload.key, skipped=payload.skipped
        )
    except OpeningCheckConflict as exc:  # 當天第一筆兩台同時進來
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return result


@router.post(
    "/items",
    response_model=OpeningCheckItemRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createOpeningCheckItem",
)
async def create_item(
    payload: OpeningCheckItemCreateRequest, session: SessionDep, user: ManagerDep
) -> OpeningCheckItemRead:
    item = await OpeningCheckService(session).create_item(
        user.store_id, label=payload.label, href=payload.href, actor_user_id=user.id
    )
    await session.commit()
    return OpeningCheckItemRead(id=item.id, label=item.label, href=item.href, done=False)


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteOpeningCheckItem",
)
async def delete_item(item_id: int, session: SessionDep, user: ManagerDep) -> None:
    """刪除＝封存：已勾過的歷史紀錄還指著它。"""
    if not await OpeningCheckService(session).archive_item(
        user.store_id, item_id, actor_user_id=user.id
    ):
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到檢查項目")
    await session.commit()
