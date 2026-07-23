import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.core.audit  # 註冊模型到 metadata（autogenerate 用）
import app.modules.acquisition.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.backup.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.campaigns.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.cashdrawer.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.consignment.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.contacts.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.customerdisplay.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.einvoice.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.inventory.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.menu.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.purchasing.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.returns.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.sales.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.settings.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.signing.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.stocktake.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.store.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.storecredit.models  # 註冊模型到 metadata（autogenerate 用）
import app.modules.user.models  # noqa: F401  # 註冊模型到 metadata（autogenerate 用）
from app.core.config import get_settings
from app.core.db import Base

# Alembic Config 物件，提供存取 .ini 內設定。
config = context.config


def _restore_target(url: str | None) -> str | None:
    """僅接受 throwaway 還原庫（lucamp_restore_*）作為升級目標;其餘一律 None（回退正式庫）。"""
    if not url:
        return None
    try:
        db = make_url(url).database
    except Exception:
        return None
    return url if db and db.startswith("lucamp_restore_") else None


# 連線字串一律來自應用設定（讀根目錄 .env），不寫死於 alembic.ini。
# 僅設定字串，不在 import 時建立連線；實際連線發生在 run_migrations_online()。
# 例外：還原演練把舊版本備份的 throwaway 庫「升級到 head」時，呼叫端會**先在傳入的 Config**
# 設好 sqlalchemy.url 指向該 throwaway 庫（per-call、非 process 全域，杜絕誤打正式庫）
# 這裡只有在該預設 url 確實指向 lucamp_restore_* 時才沿用;否則一律用正式 DATABASE_URL。
_preset = _restore_target(config.get_main_option("sqlalchemy.url"))
config.set_main_option("sqlalchemy.url", _preset or get_settings().database_url)

# 設定 Python logging。
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# autogenerate 的目標 metadata。
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """離線模式：只用 URL、不建立 Engine（可產生 SQL 而不需 DBAPI）。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """建立 async Engine 並將連線關聯到 context。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """線上模式。"""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
