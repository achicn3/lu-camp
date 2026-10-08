"""帶回家商品的線上保留（docs/63 §13、M1d）：拉單時直接扣現量並記帳，取消／到期／結帳前加回。"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inventory.models import CatalogProduct, StockMovement
from app.modules.inventory.service import InventoryService
from app.modules.store.models import Store
from app.shared.enums import StockDirection, StockReason
from app.shared.exceptions import (
    CrossStoreReference,
    InsufficientStock,
    OwnershipValidationError,
)


async def _product(session: AsyncSession, *, qty: int = 3, active: bool = True) -> CatalogProduct:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    product = CatalogProduct(
        store_id=store.id,
        sku=f"BEAN-{store.id}",
        name="耶加雪菲豆 200g",
        unit_price=Decimal(450),
        quantity_on_hand=qty,
        is_active=active,
    )
    session.add(product)
    await session.flush()
    return product


async def _movements(session: AsyncSession, product_id: int) -> list[tuple[str, str, int, int]]:
    rows = await session.scalars(
        select(StockMovement)
        .where(StockMovement.catalog_product_id == product_id)
        .order_by(StockMovement.id)
    )
    return [(m.direction.value, m.reason.value, m.qty, m.ref_id or 0) for m in rows]


async def test_hold_deducts_on_hand_and_release_adds_it_back(db_session: AsyncSession) -> None:
    product = await _product(db_session, qty=3)
    svc = InventoryService(db_session)
    await svc.hold_catalog_for_online_order(product.store_id, product.id, 2, online_order_id=77)
    await db_session.refresh(product)
    assert product.quantity_on_hand == 1  # 櫃檯不會把保留的那兩包賣掉
    await svc.release_online_hold(product.store_id, product.id, 2, online_order_id=77)
    await db_session.refresh(product)
    assert product.quantity_on_hand == 3
    assert await _movements(db_session, product.id) == [
        (StockDirection.OUT.value, StockReason.ONLINE_HOLD.value, 2, 77),
        (StockDirection.IN.value, StockReason.ONLINE_RELEASE.value, 2, 77),
    ]


async def test_hold_refuses_when_not_enough(db_session: AsyncSession) -> None:
    product = await _product(db_session, qty=1)
    with pytest.raises(InsufficientStock, match="耶加雪菲豆"):
        await InventoryService(db_session).hold_catalog_for_online_order(
            product.store_id, product.id, 2, online_order_id=1
        )
    await db_session.refresh(product)
    assert product.quantity_on_hand == 1
    assert await _movements(db_session, product.id) == []


async def test_hold_refuses_inactive_or_other_store(db_session: AsyncSession) -> None:
    product = await _product(db_session, active=False)
    with pytest.raises(InsufficientStock, match="停售"):
        await InventoryService(db_session).hold_catalog_for_online_order(
            product.store_id, product.id, 1, online_order_id=1
        )
    with pytest.raises(CrossStoreReference):
        await InventoryService(db_session).hold_catalog_for_online_order(
            product.store_id + 999, product.id, 1, online_order_id=1
        )


async def test_stocktake_counts_held_goods_as_still_on_the_shelf(db_session: AsyncSession) -> None:
    """盤點時保留的貨還在架上：實點 5、線上保留 2 → 可賣 3；
    之後取消加回才不會變 7（Codex M1d 第一輪）。"""
    product = await _product(db_session, qty=5)
    svc = InventoryService(db_session)
    await svc.hold_catalog_for_online_order(product.store_id, product.id, 2, online_order_id=9)
    delta = await svc.adjust_catalog_to_count(
        product.store_id, product.id, 5, ref_type="stocktake", ref_id=1
    )
    assert delta == 0
    await db_session.refresh(product)
    assert product.quantity_on_hand == 3
    # 實際少了一包：可賣的跟著少一包，保留不動
    assert (
        await svc.adjust_catalog_to_count(
            product.store_id, product.id, 4, ref_type="stocktake", ref_id=2
        )
        == -1
    )
    await svc.release_online_hold(product.store_id, product.id, 2, online_order_id=9)
    await db_session.refresh(product)
    assert product.quantity_on_hand == 4


async def test_stocktake_below_held_quantity_is_refused(db_session: AsyncSession) -> None:
    product = await _product(db_session, qty=5)
    svc = InventoryService(db_session)
    await svc.hold_catalog_for_online_order(product.store_id, product.id, 2, online_order_id=9)
    with pytest.raises(OwnershipValidationError, match="線上單保留"):
        await svc.adjust_catalog_to_count(
            product.store_id, product.id, 1, ref_type="stocktake", ref_id=1
        )
