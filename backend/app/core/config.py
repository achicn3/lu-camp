"""應用設定：一律從環境變數 / 根目錄 .env 讀取，程式內不寫死祕密。

金鑰一律由環境注入、無預設值（缺少即啟動失敗），確保金鑰不入 repo。
金鑰格式於啟動時即驗證（fail-fast），設錯立即失敗、不延到首次使用。
"""

import base64
import binascii
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PII_KEY_BYTES = 32

# 專案根目錄的 .env（compose 與後端共用同一份）。
# config.py 位於 backend/app/core/config.py → parents[3] 為 repo 根。
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """從環境變數讀取的設定。OS 環境變數優先於 .env 檔。"""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str
    app_env: str = "development"
    # CORS 允許來源（逗號分隔）：瀏覽器前端與 API 不同埠/主機時必須列入，
    # 否則瀏覽器一律擋（實測發現：docs/10 架構即為 LAN 前端打 FastAPI）。
    cors_origins: str = "http://localhost:3000"
    # 金鑰皆無預設、由 env 注入。pii_enc_key 為 base64 of 32 bytes；secret_key 供 JWT 簽章。
    pii_enc_key: str
    hmac_key: str
    secret_key: str

    @field_validator("pii_enc_key")
    @classmethod
    def _validate_pii_enc_key(cls, value: str) -> str:
        """PII_ENC_KEY 必須能 base64 解出 32 bytes（AES-256）。"""
        try:
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("PII_ENC_KEY 必須為合法 base64") from exc
        if len(raw) != _PII_KEY_BYTES:
            raise ValueError(f"PII_ENC_KEY 解出長度須為 {_PII_KEY_BYTES} bytes")
        return value

    @field_validator("hmac_key", "secret_key")
    @classmethod
    def _validate_non_empty(cls, value: str) -> str:
        """HMAC_KEY / SECRET_KEY 不可為空。"""
        if not value.strip():
            raise ValueError("金鑰不可為空")
        return value


@lru_cache
def get_settings() -> Settings:
    """回傳快取的設定單例。"""
    return Settings()
