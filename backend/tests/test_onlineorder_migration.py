"""線上點餐店內端 migration 的降版守衛：有桌位碼或發佈紀錄就拒絕降版。"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.onlineorder.models import OnlineTableCode
from app.modules.store.models import Store


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "da91ffee580d_online_order_publish.py"
    )
    spec = importlib.util.spec_from_file_location("online_order_publish_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_downgrade_allowed_when_empty(db_session: AsyncSession) -> None:
    for table in ("online_table_codes", "online_menu_publications"):
        await db_session.execute(text(f"DELETE FROM {table}"))
    conn = await db_session.connection()
    await conn.run_sync(lambda c: _migration().abort_if_online_data_exists(c))


async def test_downgrade_refused_with_table_codes(db_session: AsyncSession) -> None:
    store = Store(name="門市")
    db_session.add(store)
    await db_session.flush()
    db_session.add(
        OnlineTableCode(store_id=store.id, label="A1", service_mode="DINE_IN", code="x" * 22)
    )
    await db_session.flush()
    conn = await db_session.connection()
    with pytest.raises(RuntimeError, match="桌位碼 1 筆"):
        await conn.run_sync(lambda c: _migration().abort_if_online_data_exists(c))
