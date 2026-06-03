"""JWT access token 編/解碼（HS256，金鑰用 SECRET_KEY）。

僅提供 token 編解碼原語；登入 API / 使用者 CRUD / refresh 留待後續 auth 模組。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30


def encode_access_token(
    *,
    user_id: int,
    role: str,
    store_id: int,
    expires_delta: timedelta | None = None,
) -> str:
    """簽發 access token；payload 含 sub(user_id)、role、store_id。"""
    now = datetime.now(UTC)
    ttl = (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    expire = now + ttl
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "store_id": store_id,
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """驗證並解碼 token；過期/簽章錯誤會擲出對應的 jwt 例外。"""
    return jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])
