"""備份/還原狀態表（docs/31）：對帳、健康度、清單、稽核來源。

`backup_runs` 每次備份一列；同店至多一列 RUNNING（部分唯一索引，單一在跑守衛）。
`restore_runs` 每次還原一列（高危、一律留痕）。兩表**不含 PII / 金鑰 / R2 憑證**——僅檔名/雜湊/
大小/狀態/時間/觸發者，與備份檔本身分開（金鑰缺一即廢的兩組祕密都在店外，不入 DB）。
列舉以 native_enum=False + CHECK 儲存（比照全庫慣例）。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin
from app.shared.enums import BackupStatus, BackupTrigger, RestoreStatus


def _enum_col(enum_type: type) -> Enum:
    return Enum(enum_type, native_enum=False, length=20, create_constraint=True)


class BackupRun(Base, TimestampMixin):
    """一次備份執行（docs/31 §2/§4）。成功時填 file_name/r2_key/size/sha256；失敗填 last_error。"""

    __tablename__ = "backup_runs"
    __table_args__ = (
        # 單一在跑守衛：同店至多一列 RUNNING（部分唯一索引）——tick 與手動撞在一起只一個進行。
        Index(
            "uq_backup_runs_one_running",
            "store_id",
            unique=True,
            postgresql_where=("status = 'RUNNING'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    trigger: Mapped[BackupTrigger] = mapped_column(_enum_col(BackupTrigger))
    status: Mapped[BackupStatus] = mapped_column(_enum_col(BackupStatus))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    db_name: Mapped[str] = mapped_column(String(63))
    file_name: Mapped[str | None] = mapped_column(String(200))
    r2_key: Mapped[str | None] = mapped_column(String(300))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    # 手動觸發者（null＝排程 tick）。
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class RestoreRun(Base, TimestampMixin):
    """一次還原執行（docs/31 §6）：下載→解密→還原到全新庫→四驗。VERIFIED 才可切換（受控腳本）。"""

    __tablename__ = "restore_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    status: Mapped[RestoreStatus] = mapped_column(_enum_col(RestoreStatus))
    source_r2_key: Mapped[str] = mapped_column(String(300))
    restore_db_name: Mapped[str] = mapped_column(String(63))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 四驗結果（alembic/表數/簽名 sha256 抽驗/起後端）——結構化留痕，供 UI 呈現。
    verifications: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_error: Mapped[str | None] = mapped_column(Text)
    actor_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
