"""auth 路由：POST /auth/login（帳密 → JWT access token）。

只做 I/O 與驗證（§2）；認證邏輯在 UserService。帳號不存在／密碼錯誤／已停用
一律回相同 401 訊息（防使用者列舉，配合 service 的等時化驗證）。
refresh token 留待 D-4 auth 強化（access token 期限見 core/security）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.modules.user.schemas import LoginRequest, TokenResponse
from app.modules.user.service import UserService

router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_LOGIN_FAILED_DETAIL = "帳號或密碼錯誤"  # 三種失敗共用，不洩漏帳號是否存在


@router.post("/login", response_model=TokenResponse, operation_id="login")
async def login(payload: LoginRequest, session: SessionDep) -> TokenResponse:
    user = await UserService(session).authenticate(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_LOGIN_FAILED_DETAIL)
    token = encode_access_token(user_id=user.id, role=user.role.value, store_id=user.store_id)
    return TokenResponse(access_token=token)
