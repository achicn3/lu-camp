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
    # 登入永不過期（使用者裁示 2026-06-18）：簽發 access token 時省略 exp。
    # ⚠️ 此舉與 CLAUDE.md §5「JWT 短效」相牴觸——被降權/停用/離職者在登出前可永久存取，
    # 故 D-4（敏感操作於伺服器端重驗 role/is_active）成為必要緩解。設 false 即恢復短效 token。
    auth_session_never_expires: bool = True
    # 金鑰皆無預設、由 env 注入。pii_enc_key 為 base64 of 32 bytes；secret_key 供 JWT 簽章。
    pii_enc_key: str
    hmac_key: str
    secret_key: str
    # Amego 光貿電子發票（docs/24）：App Key 走環境變數、不入 repo/DB；空字串＝未設定，
    # 未設定時不可啟用 einvoice_enabled、也不可送單。測試/正式同一 API 網址。
    amego_app_key: str = ""
    amego_api_base: str = "https://invoice-api.amego.tw"
    # LINE Pay Offline API v4（docs/30）：Channel 憑證走環境變數、不入 repo/DB；空字串＝未設定，
    # 未設定或 settings.linepay_enabled=False 時，帶 LINE_PAY tender 的結帳一律 fail-closed 拒絕。
    # 沙盒/正式以不同 base_url 區分（預設沙盒；正式以 env 覆寫為 https://api-pay.line.me）。
    linepay_channel_id: str = ""
    linepay_channel_secret: str = ""
    linepay_api_base: str = "https://sandbox-api-pay.line.me"
    # 備份系統（docs/31）：R2 憑證與 AES 口令走 .env.r2、不入 repo/DB；空字串＝未設定，
    # 未設定時排程 tick 不會嘗試備份（改由健康度頁告警），手動觸發則回錯。
    # 部署時以 `set -a; source .env.r2; set +a` 注入下列 R2_* 與 BACKUP_PASSPHRASE。
    r2_endpoint: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "pos"
    # 與 .env.r2 命名一致（R2_BACKUP_PASSPHRASE）：AES 加密口令,不入 repo/DB。
    r2_backup_passphrase: str = ""
    # 備份執行的本機參數（docs/28 流程：docker exec 進 postgres 容器跑 pg_dump）。
    backup_docker_bin: str = "docker"
    backup_db_container: str = "lu-camp-db-1"
    backup_local_dir: str = "/home/test/lu-camp-backups"
    # 排程 tick：行程內背景任務的主開關與喚醒間隔（秒）。到期判斷另看 settings.backup_*。
    backup_scheduler_enabled: bool = True
    backup_tick_seconds: int = 900

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
