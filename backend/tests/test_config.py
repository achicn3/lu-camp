"""core/config.py — Settings 從環境變數讀取設定。"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def _set_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """設定所有必填欄位（金鑰無預設值）。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/db")
    monkeypatch.setenv("PII_ENC_KEY", "dGVzdC1rZXktMzItYnl0ZXMtZm9yLXVuaXR0ZXN0MQ==")
    monkeypatch.setenv("HMAC_KEY", "test-hmac-key")


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
    monkeypatch.delenv("PII_ENC_KEY", raising=False)
    monkeypatch.delenv("HMAC_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
