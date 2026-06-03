"""稽核紀錄：append-only 寫入，且不記錄 PII 明文。

- append-only：本模組只提供寫入（insert），不提供更新/刪除；紀錄無 updated_at，視為不可變。
- 不記 PII 明文：before/after 在寫入前對敏感鍵做遮罩（_redact）。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# 永不寫入稽核的敏感鍵（PII 明文 / 祕密）。
SENSITIVE_KEYS = frozenset(
    {
        "national_id",
        "national_id_enc",
        "national_id_blind_index",
        "password",
        "password_hash",
    }
)
_REDACTED = "***REDACTED***"


class AuditLog(Base):
    """稽核紀錄（append-only，不可變；無 updated_at）。"""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    is_sensitive: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


def _redact(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """將敏感鍵的值換成遮罩字串；非敏感欄位保留。"""
    if data is None:
        return None
    return {k: (_REDACTED if k in SENSITIVE_KEYS else v) for k, v in data.items()}


async def write_audit_log(
    session: AsyncSession,
    *,
    store_id: int,
    actor_user_id: int | None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    is_sensitive: bool = False,
) -> AuditLog:
    """寫入一筆稽核紀錄（insert-only）；before/after 自動遮罩敏感鍵。

    before/after 應為扁平 dict；遮罩僅作用於頂層鍵（不遞迴巢狀結構）。
    """
    entry = AuditLog(
        store_id=store_id,
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=_redact(before),
        after=_redact(after),
        is_sensitive=is_sensitive,
    )
    session.add(entry)
    await session.flush()
    return entry
