"""資料庫連線：async engine 與 session。

唯一在此建立 engine / sessionmaker；各模組 repository 透過 get_session 取得 session。
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

engine = create_async_engine(get_settings().database_url)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession]:
    """FastAPI 依賴：產出一個 async session，結束時自動關閉。"""
    async with AsyncSessionLocal() as session:
        yield session
