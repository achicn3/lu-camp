"""菜單照片 migration 的降版守衛：有照片就拒絕降版，不靜默丟掉圖檔。"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.menu.models import MenuPhoto
from app.modules.store.models import Store


def _migration() -> ModuleType:
    path = Path(__file__).parents[1] / "alembic" / "versions" / "2e52d783ec0e_menu_photos.py"
    spec = importlib.util.spec_from_file_location("menu_photos_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_downgrade_guard_allows_without_photos(db_session: AsyncSession) -> None:
    await db_session.execute(text("UPDATE menu_items SET photo_sha256 = NULL"))
    await db_session.execute(text("DELETE FROM menu_photos"))
    conn = await db_session.connection()
    await conn.run_sync(lambda c: _migration().abort_if_photos_exist(c))


async def test_downgrade_guard_aborts_with_photos(db_session: AsyncSession) -> None:
    store = Store(name="門市")
    db_session.add(store)
    await db_session.flush()
    db_session.add(
        MenuPhoto(store_id=store.id, sha256="a" * 64, content=b"webp", width=1, height=1)
    )
    await db_session.flush()
    conn = await db_session.connection()
    with pytest.raises(RuntimeError, match="菜單照片 1 張"):
        await conn.run_sync(lambda c: _migration().abort_if_photos_exist(c))
