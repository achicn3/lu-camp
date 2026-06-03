"""core/security.py — JWT access token 編/解碼。"""

from datetime import timedelta

import jwt
import pytest

from app.core.security import decode_access_token, encode_access_token


def test_encode_decode_roundtrip() -> None:
    token = encode_access_token(user_id=1, role="MANAGER", store_id=2)
    payload = decode_access_token(token)
    assert payload["sub"] == "1"
    assert payload["role"] == "MANAGER"
    assert payload["store_id"] == 2


def test_expired_token_rejected() -> None:
    token = encode_access_token(
        user_id=1, role="CLERK", store_id=1, expires_delta=timedelta(minutes=-1)
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)


def test_tampered_token_rejected() -> None:
    token = encode_access_token(user_id=1, role="CLERK", store_id=1)
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token + "tamper")
