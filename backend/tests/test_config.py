"""core/config.py — Settings 從環境變數讀取設定 + 金鑰 fail-fast 驗證。"""

import base64

import pytest
from pydantic import ValidationError

from app.core.config import Settings

_VALID_PII_KEY = base64.b64encode(b"0" * 32).decode()  # 合法 base64 of 32 bytes


def _set_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """設定所有必填欄位（金鑰無預設值）。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/db")
    monkeypatch.setenv("PII_ENC_KEY", _VALID_PII_KEY)
    monkeypatch.setenv("HMAC_KEY", "test-hmac-key")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")


def test_database_url_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    settings = Settings(_env_file=None)
    assert settings.database_url == "postgresql+asyncpg://u:p@h:5432/db"


def test_app_env_defaults_to_development(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.delenv("APP_ENV", raising=False)
    settings = Settings(_env_file=None)
    assert settings.app_env == "development"


def test_app_env_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("APP_ENV", "test")
    settings = Settings(_env_file=None)
    assert settings.app_env == "test"


def test_keys_have_no_insecure_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """金鑰無預設值：未提供即啟動失敗（確保不靠程式內預設金鑰）。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/db")
    for key in ("PII_ENC_KEY", "HMAC_KEY", "SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_pii_key_must_decode_to_32_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """PII_ENC_KEY 長度錯誤 → 啟動即失敗（fail-fast）。"""
    _set_required(monkeypatch)
    monkeypatch.setenv("PII_ENC_KEY", base64.b64encode(b"too-short").decode())
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_pii_key_must_be_valid_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("PII_ENC_KEY", "!!!not-base64!!!")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_secret_key_must_not_be_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required(monkeypatch)
    monkeypatch.setenv("SECRET_KEY", "   ")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
