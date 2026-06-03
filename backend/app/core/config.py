"""應用設定：一律從環境變數 / 根目錄 .env 讀取，程式內不寫死祕密。

金鑰一律由環境注入、無預設值（缺少即啟動失敗），確保金鑰不入 repo。
SECRET_KEY（JWT）尚未被程式使用，待 auth 導入時再加入。
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
    # PII 欄位加密金鑰（base64 of 32 bytes）與 blind-index HMAC 金鑰；無預設、由 env 注入。
    pii_enc_key: str
    hmac_key: str


@lru_cache
def get_settings() -> Settings:
    """回傳快取的設定單例。"""
    return Settings()
