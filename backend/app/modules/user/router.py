"""auth 路由：POST /auth/login（帳密 → JWT access token）。

只做 I/O 與驗證（§2）；認證邏輯在 UserService。帳號不存在／密碼錯誤／已停用
一律回相同 401 訊息（防使用者列舉，配合 service 的等時化驗證）。
登入節流（429 + Retry-After）在密碼驗證**之前**執行（防暴力破解/CPU 耗盡），
失敗與節流事件寫結構化安全日誌（不含密碼）。
refresh token 留待 D-4 auth 強化（access token 期限見 core/security）。
"""

import logging
import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.core.security import encode_access_token
from app.modules.user.schemas import CurrentUserResponse, LoginRequest, TokenResponse
from app.modules.user.service import UserService
from app.modules.user.throttle import LoginThrottle

router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_LOGIN_FAILED_DETAIL = "帳號或密碼錯誤"  # 三種失敗共用，不洩漏帳號是否存在
_THROTTLED_DETAIL = "嘗試次數過多，請稍後再試"

_security_log = logging.getLogger("app.security")

_throttle_singleton = LoginThrottle()


def get_login_throttle() -> LoginThrottle:
    """登入節流器（模組層單例；測試以 dependency_overrides 換新實例隔離）。"""
    return _throttle_singleton


ThrottleDep = Annotated[LoginThrottle, Depends(get_login_throttle)]


def _client_ip(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


@router.post("/login", response_model=TokenResponse, operation_id="login")
async def login(
    payload: LoginRequest, session: SessionDep, request: Request, throttle: ThrottleDep
) -> TokenResponse:
    ip = _client_ip(request)
    retry_after = throttle.retry_after(payload.username, ip)
    if retry_after is not None:
        # 鎖定中：在進入任何雜湊運算前就擋下
        _security_log.warning(
            "login throttled username=%s ip=%s retry_after=%.0f", payload.username, ip, retry_after
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=_THROTTLED_DETAIL,
            headers={"Retry-After": str(math.ceil(retry_after))},
        )
    user = await UserService(session).authenticate(payload.username, payload.password)
    if user is None:
        throttle.record_failure(payload.username, ip)
        _security_log.warning("login failed username=%s ip=%s", payload.username, ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_LOGIN_FAILED_DETAIL)
    throttle.record_success(payload.username, ip)
    token = encode_access_token(user_id=user.id, role=user.role.value, store_id=user.store_id)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=CurrentUserResponse, operation_id="getCurrentUser")
async def get_me(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUserResponse:
    """目前登入者（role 取自 DB 現值）——前端導覽依此收斂，不信任 token 的 role claim。"""
    return CurrentUserResponse(id=user.id, role=user.role, store_id=user.store_id)
