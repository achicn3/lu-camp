"""測試共用 fixtures。

DB 隔離策略：用本機 compose 起的 PostgreSQL（非 testcontainers，見 docs/06），
以「外層交易包覆 + session 走 savepoint」達成測試間隔離：
- 每個測試在獨立的外層交易中執行，結束時 rollback，資料不落地、測試間不互相污染。
- session 以 join_transaction_mode="create_savepoint" 加入外層交易，
  因此即使測試內呼叫 commit()，也只是釋放 savepoint，外層 rollback 仍會整批丟棄。

測試用 engine 採 NullPool：每條連線用畢即關，避免連線在不同 event loop 間被重用。
"""

from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.core.audit  # 註冊模型到 Base.metadata
import app.core.db as app_db
import app.modules.acquisition.models  # 註冊模型到 Base.metadata
import app.modules.cashdrawer.models  # 註冊模型到 Base.metadata
import app.modules.consignment.models  # 註冊模型到 Base.metadata
import app.modules.contacts.models  # 註冊模型到 Base.metadata
import app.modules.inventory.models  # 註冊模型到 Base.metadata
import app.modules.sales.models  # 註冊模型到 Base.metadata
import app.modules.settings.models  # 註冊模型到 Base.metadata
import app.modules.store.models  # 註冊模型到 Base.metadata
import app.modules.user.models  # noqa: F401  # 註冊模型到 Base.metadata
from app.core.config import get_settings
from app.core.db import Base

test_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _create_app_schema() -> AsyncGenerator[None]:
    """為測試建立應用 schema（session 結束時移除，不留殘餘）。"""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _rollback_probe_table() -> AsyncGenerator[None]:
    """供回滾隔離驗證用的暫存表；session 結束時移除，不在 DB 留殘餘。"""
    async with test_engine.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS _rollback_probe (id integer)"))
    yield
    async with test_engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS _rollback_probe"))


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession]:
    """產出一個與 DB 隔離的 session：測試結束自動 rollback。"""
    connection = await test_engine.connect()
    trans = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await connection.close()


@pytest_asyncio.fixture(autouse=True)
async def _dispose_app_engine() -> AsyncGenerator[None]:
    """每個測試後釋放正式 engine 的連線池並清快取，避免連線跨 event loop 重用。"""
    yield
    await app_db.get_engine().dispose()
    app_db.get_sessionmaker.cache_clear()
    app_db.get_engine.cache_clear()
