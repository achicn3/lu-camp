"""彈性菜單 migration 的降版守衛：有選項群組／選項／品項介紹就拒絕降版，不靜默丟資料。"""

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.menu.models import MenuItem, MenuOptionGroup
from app.modules.store.models import Store


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "0a577011b3c1_menu_flexible_options.py"
    )
    spec = importlib.util.spec_from_file_location("menu_flexible_options_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _clear(session: AsyncSession) -> None:
    await session.execute(text("DELETE FROM menu_item_option_groups"))
    await session.execute(text("DELETE FROM menu_options"))
    await session.execute(text("DELETE FROM menu_option_groups"))
    await session.execute(text("UPDATE menu_items SET description = NULL"))


async def test_downgrade_guard_allows_when_no_option_data(db_session: AsyncSession) -> None:
    migration = _migration()
    await _clear(db_session)
    conn = await db_session.connection()
    await conn.run_sync(lambda c: migration.abort_if_option_data_exists(c))


async def test_downgrade_guard_aborts_on_groups(db_session: AsyncSession) -> None:
    migration = _migration()
    await _clear(db_session)
    store = Store(name="降版守衛測試店")
    db_session.add(store)
    await db_session.flush()
    db_session.add(MenuOptionGroup(store_id=store.id, name="溫度", min_select=1, max_select=1))
    await db_session.flush()
    conn = await db_session.connection()
    with pytest.raises(RuntimeError, match=r"拒絕降版.*menu_option_groups 1 筆"):
        await conn.run_sync(lambda c: migration.abort_if_option_data_exists(c))


async def test_downgrade_guard_aborts_on_description(db_session: AsyncSession) -> None:
    migration = _migration()
    await _clear(db_session)
    store = Store(name="降版守衛測試店2")
    db_session.add(store)
    await db_session.flush()
    db_session.add(
        MenuItem(store_id=store.id, name="美式", unit_price=Decimal(120), description="堅果調")
    )
    await db_session.flush()
    conn = await db_session.connection()
    with pytest.raises(RuntimeError, match=r"menu_items.description 1 筆"):
        await conn.run_sync(lambda c: migration.abort_if_option_data_exists(c))
