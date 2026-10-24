"""一般商品手動調整數量原因的降版守衛（店主 2026-10-24）。

舊程式認不得 DAMAGED 等異動原因：已有這種帳就不能降，否則讀明細歷史會炸。
乾淨資料庫的正常回滾不能被擋。
"""

import importlib.util
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inventory.models import CatalogProduct, StockMovement
from app.modules.store.models import Store
from app.shared.enums import ItemKind, StockDirection, StockReason


def _migration() -> ModuleType:
    path = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "f8c4a2e6d0b9_catalog_quantity_adjust_reasons.py"
    )
    spec = importlib.util.spec_from_file_location("catalog_quantity_adjust_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_downgrade_guard_allows_when_no_adjustments(db_session: AsyncSession) -> None:
    migration = _migration()
    conn = await db_session.connection()
    await conn.run_sync(lambda sync_conn: migration.abort_if_adjustments_exist(sync_conn))


async def test_downgrade_guard_aborts_when_adjustment_exists(db_session: AsyncSession) -> None:
    migration = _migration()
    store = Store(name="降版守衛測試店")
    db_session.add(store)
    await db_session.flush()
    product = CatalogProduct(
        store_id=store.id, sku="MIG-ADJ-1", name="罐裝咖啡", unit_price=Decimal(100)
    )
    db_session.add(product)
    await db_session.flush()
    db_session.add(
        StockMovement(
            store_id=store.id,
            item_kind=ItemKind.CATALOG,
            direction=StockDirection.OUT,
            qty=1,
            reason=StockReason.DAMAGED,
            catalog_product_id=product.id,
        )
    )
    await db_session.flush()

    conn = await db_session.connection()
    with pytest.raises(RuntimeError, match=r"拒絕降版.*1 筆手動調整"):
        await conn.run_sync(lambda sync_conn: migration.abort_if_adjustments_exist(sync_conn))
