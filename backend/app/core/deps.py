"""共用依賴：目前使用者（從 JWT Bearer 解出並回 DB 覆核）與角色檢查。

RBAC 真實生效：無/壞/過期 token → 401；角色不符 → 403。不留假樁。

D-4（永不過期 token 的必要緩解）：token 簽發後可永久有效（CLAUDE.md §5 例外、使用者裁示），
故每次請求都必須回資料庫覆核呼叫者現況——被停用/刪除者一律 401，且角色/store 以 DB 現值為準
（token claim 僅作識別），降權即時生效，不等 token 過期。跨模組僅透過 user service（§2）。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import decode_access_token
from app.modules.user.service import UserService

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    """呼叫者身分；role/store_id 取自 DB 現值（非 token claim）。"""

    id: int
    role: str
    store_id: int


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CurrentUser:
    """解析 Bearer token 並回 DB 覆核；缺/壞/過期 token 或使用者已停用/不存在一律 401。"""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供認證憑證")
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="無效或過期的憑證"
        ) from exc
    # D-4：永不過期 token 需逐請求覆核——以 DB 現況判定，被停用/刪除者拒絕，角色以 DB 為準。
    user = await UserService(session).get_user_in_store(
        store_id=int(payload["store_id"]), user_id=int(payload["sub"])
    )
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="使用者已停用或不存在")
    return CurrentUser(id=user.id, role=user.role.value, store_id=user.store_id)


def require_role(role: str) -> Callable[[CurrentUser], Awaitable[CurrentUser]]:
    """產生「要求特定角色」的依賴；角色不符回 403。"""

    async def _checker(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if user.role != role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="權限不足")
        return user

    return _checker
