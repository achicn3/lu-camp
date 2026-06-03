"""應用設定：一律從環境變數 / 根目錄 .env 讀取，程式內不寫死祕密。

目前僅納入 T1 實際使用的欄位（database_url / app_env）；
金鑰類（SECRET_KEY / PII_ENC_KEY / HMAC_KEY）等到實際被程式使用時再加入。
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

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


@lru_cache
def get_settings() -> Settings:
    """回傳快取的設定單例。"""
    return Settings()
