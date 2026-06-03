"""共用依賴：目前使用者（從 JWT Bearer 解出）與角色檢查。

RBAC 真實生效：無/壞/過期 token → 401；角色不符 → 403。不留假樁。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    """由 token 解出的呼叫者身分（不查 DB）。"""

    id: int
    role: str
    store_id: int


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    """解析 Bearer token → CurrentUser；缺/壞/過期 token 一律 401。"""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供認證憑證")
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="無效或過期的憑證"
        ) from exc
    return CurrentUser(
        id=int(payload["sub"]), role=payload["role"], store_id=int(payload["store_id"])
    )


def require_role(role: str) -> Callable[[CurrentUser], Awaitable[CurrentUser]]:
    """產生「要求特定角色」的依賴；角色不符回 403。"""

    async def _checker(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if user.role != role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="權限不足")
        return user

    return _checker
