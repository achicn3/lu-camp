"""測試共用 fixtures。

DB 隔離策略：用本機 compose 起的 PostgreSQL（非 testcontainers，見 docs/06），
以「外層交易包覆 + session 走 savepoint」達成測試間隔離：
- 每個測試在獨立的外層交易中執行，結束時 rollback，資料不落地、測試間不互相污染。
- session 以 join_transaction_mode="create_savepoint" 加入外層交易，
  因此即使測試內呼叫 commit()，也只是釋放 savepoint，外層 rollback 仍會整批丟棄。

測試用 engine 採 NullPool：每條連線用畢即關，避免連線在不同 event loop 間被重用。
"""

# ruff: noqa: I001  # db_safety 必須在任何 app DB 模組 import 前改寫 DATABASE_URL。

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

# 測試若指向一般開發／正式庫，後面的 drop_all、TRUNCATE 與停用 trigger 會毀掉真實帳。
# 在載入任何會建立 app engine 的模組前，從設定 URL 派生本 pytest process 專用資料庫。
from tests.db_safety import BASE_DATABASE_URL, TEST_DATABASE_NAME

import app.core.db as app_db
from app.core.config import get_settings

test_engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
_ALEMBIC_INI = str(Path(__file__).resolve().parents[1] / "alembic.ini")


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _create_app_schema() -> AsyncGenerator[None]:
    """建立 process 專用測試 DB；任何 trigger bypass／TRUNCATE 都不會碰原始資料庫。"""
    admin_engine = create_async_engine(
        BASE_DATABASE_URL,
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
    )
    async with admin_engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": TEST_DATABASE_NAME},
        )
        if exists:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": TEST_DATABASE_NAME},
            )
            await conn.execute(text(f'DROP DATABASE "{TEST_DATABASE_NAME}"'))
        await conn.execute(text(f'CREATE DATABASE "{TEST_DATABASE_NAME}"'))
    # 真正從空庫跑完整 migration 鏈，而非 ORM create_all 後假裝在 head。這同時驗證
    # revision 可部署、非 metadata DDL（trigger/function）齊全，並建立真 alembic_version。
    alembic_config = Config(_ALEMBIC_INI)
    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    try:
        yield
    finally:
        await test_engine.dispose()
        await app_db.get_engine().dispose()
        async with admin_engine.connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": TEST_DATABASE_NAME},
            )
            await conn.execute(text(f'DROP DATABASE "{TEST_DATABASE_NAME}"'))
        await admin_engine.dispose()


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
