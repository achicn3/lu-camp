"""auth/login 輸入輸出 schema（router 層 I/O 驗證）。"""

from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """登入請求；長度上限鏡像 users 欄位，避免無意義長字串打到 DB/雜湊。"""

    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    """登入成功回應：JWT access token（payload 含 sub/role/store_id）。"""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
