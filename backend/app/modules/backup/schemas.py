"""backup API schema（docs/31 §5/§6）：清單/健康度/還原輸出。不含祕密（僅檔名/雜湊/大小/狀態）。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.shared.enums import BackupStatus, BackupTrigger, RestoreStatus


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


class RestoreRunRead(BaseModel):
    """一次還原執行的輸出（docs/31 §6）：四驗結果供 UI 呈現;VERIFIED 才代表救得回。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: RestoreStatus
    source_r2_key: str
    restore_db_name: str
    started_at: datetime
    finished_at: datetime | None
    verifications: dict[str, Any] | None
    last_error: str | None
    actor_user_id: int


class RestoreTriggerRequest(BaseModel):
    """觸發還原（高危,強卡控）：需 MANAGER＋打字確認（confirm_text＝該備份檔名）＋知情勾選。"""

    source_r2_key: str = Field(min_length=1, max_length=300)
    # 打字確認：須等於 source_r2_key 的檔名（綁定到「這一份」，避免還原到錯的檔）。
    confirm_text: str = Field(min_length=1, max_length=300)
    # 知情勾選：了解「還原到獨立驗證庫、不影響現行資料;切換需另跑受控腳本」。
    acknowledge: bool
