"""寄售帳篷預售 migration 的降版守衛（docs/102）：有預售就拒絕，不靜默丟資料。"""

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.onlineorder.models import OnlineTentListing
from app.modules.store.models import Store


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1] / "alembic" / "versions" / "47218f0ac027_online_tent_presales.py"
    )
    spec = importlib.util.spec_from_file_location("tent_presale_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_downgrade_guard_allows_when_no_presales(db_session: AsyncSession) -> None:
    conn = await db_session.connection()
    await conn.run_sync(lambda sync_conn: _migration().abort_if_presales_exist(sync_conn))


async def test_downgrade_guard_aborts_when_presale_exists(db_session: AsyncSession) -> None:
    store = Store(name="預售降版測試店")
    db_session.add(store)
    await db_session.flush()
    db_session.add(
        OnlineTentListing(
            store_id=store.id,
            presale_code=f"P{store.id}-0000000001",
            presale_name="NEW X5",
            presale_price=Decimal(14999),
        )
    )
    await db_session.flush()
    conn = await db_session.connection()
    migration = _migration()
    with pytest.raises(RuntimeError, match="預售"):
        await conn.run_sync(lambda sync_conn: migration.abort_if_presales_exist(sync_conn))
