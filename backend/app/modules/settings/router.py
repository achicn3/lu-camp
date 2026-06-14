"""settings 路由：讀取（任何登入者）與更新（MANAGER）。

PATCH 的範圍驗證由 SettingsUpdateRequest 在邊界完成（超出範圍回 422）；
成功後 commit。讀取端不寫 DB（未建列時回 defaults 組成的暫態值）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.settings.schemas import (
    PremiumRateHistoryRead,
    SettingsRead,
    SettingsUpdateRequest,
)
from app.modules.settings.service import StoreSettingsService
from app.shared.enums import UserRole
from app.shared.exceptions import InvalidPremiumRate

router = APIRouter(prefix="/settings", tags=["settings"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role(UserRole.MANAGER.value))]


@router.get("", response_model=SettingsRead, operation_id="getSettings")
async def get_settings(session: SessionDep, user: CurrentUserDep) -> SettingsRead:
    settings = await StoreSettingsService(session).get_effective_settings(user.store_id)
    return SettingsRead.from_model(settings)


@router.patch("", response_model=SettingsRead, operation_id="updateSettings")
async def update_settings(
    payload: SettingsUpdateRequest, session: SessionDep, user: ManagerDep
) -> SettingsRead:
    try:
        settings = await StoreSettingsService(session).update_settings(
            user.store_id, actor_user_id=user.id, patch=payload
        )
    except InvalidPremiumRate as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await session.commit()
    return SettingsRead.from_model(settings)


@router.get(
    "/premium-rate/history",
    response_model=list[PremiumRateHistoryRead],
    operation_id="getPremiumRateHistory",
)
async def premium_rate_history(
    session: SessionDep,
    user: ManagerDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PremiumRateHistoryRead]:
    rows = await StoreSettingsService(session).list_premium_history(
        user.store_id, limit=limit, offset=offset
    )
    return [PremiumRateHistoryRead.from_model(row) for row in rows]
