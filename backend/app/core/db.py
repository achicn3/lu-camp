"""資料庫連線與 ORM 基底。

engine / sessionmaker 採 lazy 建立（首次使用才建），因此只 import 本模組
（例如 Alembic env.py 取用 Base.metadata、或產生文件）不會連線、也不要求 DATABASE_URL。
唯一在此建立 engine / sessionmaker；各模組 repository 透過 get_session 取得 session。
"""

from collections.abc import AsyncGenerator
from datetime import datetime
from functools import lru_cache

from sqlalchemy import DateTime, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的宣告式基底。"""


class TimestampMixin:
    """共用時戳欄位。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


@lru_cache
def get_engine() -> AsyncEngine:
    """建立並快取 async engine（首次呼叫才建立）。"""
    return create_async_engine(get_settings().database_url)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """建立並快取 session factory。"""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession]:
    """FastAPI 依賴：產出一個 async session，結束時自動關閉。"""
    async with get_sessionmaker()() as session:
        yield session
