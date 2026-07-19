"""backup API schema（docs/31 §5）：清單/健康度輸出。不含祕密（僅檔名/雜湊/大小/狀態）。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.shared.enums import BackupStatus, BackupTrigger


class BackupRunRead(BaseModel):
    """一次備份執行的輸出（清單用）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    trigger: BackupTrigger
    status: BackupStatus
    started_at: datetime
    finished_at: datetime | None
    db_name: str
    file_name: str | None
    r2_key: str | None
    size_bytes: int | None
    sha256: str | None
    last_error: str | None
    actor_user_id: int | None


class BackupHealthRead(BaseModel):
    """備份健康度（docs/31 §5）：儀表板頂部一眼看「備份還健不健康」。"""

    model_config = ConfigDict(from_attributes=True)

    enabled: bool
    interval_hours: int
    retention: int
    offpeak_hour: int
    last_success_at: datetime | None
    last_success_age_hours: float | None  # None＝從未成功過
    due_now: bool  # 目前是否已到期（尚未跑）
    running: bool  # 是否正有一筆在跑
