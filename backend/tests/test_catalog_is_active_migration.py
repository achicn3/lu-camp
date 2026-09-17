"""`catalog_products.is_active`（停售）降版守衛（2026-09-17）。

這一欄拿掉的後果不是「少一個欄位」，而是**所有停售商品瞬間回到 POS 變成可售**，
而且「哪些被停售」再也復原不了。守衛要擋得住，但也不能擋掉乾淨資料庫的正常回滾。
"""

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inventory.models import CatalogProduct
from app.modules.store.models import Store


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "c5e2a9d47f10_catalog_product_is_active.py"
    )
    spec = importlib.util.spec_from_file_location("catalog_is_active_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_downgrade_guard_allows_drop_when_nothing_discontinued(
    db_session: AsyncSession,
) -> None:
    """沒有人停售過就可以降版——守衛不能擋掉正常回滾。"""
    migration = _migration()
    await db_session.execute(text("UPDATE catalog_products SET is_active = true"))
    conn = await db_session.connection()
    await conn.run_sync(lambda sync_conn: migration.abort_if_discontinued_exist(sync_conn))


async def test_downgrade_guard_aborts_when_something_is_discontinued(
    db_session: AsyncSession,
) -> None:
    """只要有一件停售商品就必須中止，訊息要說清楚後果。"""
    migration = _migration()
    store = Store(name="降版守衛測試店")
    db_session.add(store)
    await db_session.flush()
    db_session.add(
        CatalogProduct(
            store_id=store.id,
            sku="MIG-INACTIVE-1",
            name="不賣了的瓦斯罐",
            unit_price=Decimal(100),
            quantity_on_hand=0,
            is_active=False,
        )
    )
    await db_session.flush()

    conn = await db_session.connection()
    with pytest.raises(RuntimeError, match=r"拒絕降版.*1 件停售商品"):
        await conn.run_sync(lambda sync_conn: migration.abort_if_discontinued_exist(sync_conn))
