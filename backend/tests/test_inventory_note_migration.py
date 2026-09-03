"""商品備註 migration 的降版策略：有備註就拒絕，不靜默丟資料。

備註是店員手打、無處可還原的內容。DROP 之後再 upgrade 回來只剩空欄位，
「缺充電線」「先別賣」會靜默消失（Codex 對抗式審查第二輪 medium）。
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inventory.models import SerializedItem
from app.modules.store.models import Store
from app.shared.enums import Grade, OwnershipType


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "b7e2c9a4f1d6_inventory_item_note.py"
    )
    spec = importlib.util.spec_from_file_location("inventory_item_note_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_downgrade_guard_allows_drop_when_no_notes(db_session: AsyncSession) -> None:
    """乾淨資料庫（沒有人寫過備註）可以降版——守衛不能擋掉正常回滾。"""
    migration = _migration()
    await db_session.execute(text("UPDATE serialized_items SET note = NULL"))
    await db_session.execute(text("UPDATE catalog_products SET note = NULL"))
    await db_session.execute(text("UPDATE bulk_lots SET note = NULL"))
    conn = await db_session.connection()
    await conn.run_sync(lambda sync_conn: migration.abort_if_notes_exist(sync_conn))


async def test_downgrade_guard_aborts_when_notes_exist(db_session: AsyncSession) -> None:
    """只要有一筆備註就必須中止，且錯誤訊息要指出是哪張表、幾筆。"""
    migration = _migration()
    store = Store(name="降版守衛測試店")
    db_session.add(store)
    await db_session.flush()
    db_session.add(
        SerializedItem(
            store_id=store.id,
            item_code="MIG-NOTE-1",
            name="帳篷",
            grade=Grade.A,
            ownership_type=OwnershipType.OWNED,
            listed_price=1000,
            acquisition_cost=500,
            note="缺營釘，交貨前要說",
        )
    )
    await db_session.flush()

    conn = await db_session.connection()
    with pytest.raises(RuntimeError, match=r"拒絕降版.*serialized_items 1 筆"):
        await conn.run_sync(lambda sync_conn: migration.abort_if_notes_exist(sync_conn))
