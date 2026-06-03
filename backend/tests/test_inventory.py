"""inventory 領域測試（對齊 T5 必測 1、3、4、6 + item_code 唯一）。

定價（必測 5）見 test_money.py；併發（必測 2、3）見
tests/integration/test_inventory_concurrency.py。
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.inventory.service import InventoryService
from app.modules.store.models import Store
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    Grade,
    OwnershipType,
    SerializedItemStatus,
)
from app.shared.exceptions import (
    InsufficientStock,
    InvalidStateTransition,
    OwnershipValidationError,
)


async def _make_store(session: AsyncSession) -> int:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    return store.id


async def _owned_item(svc: InventoryService, store_id: int, code: str = "IT-1") -> SerializedItem:
    return await svc.create_serialized_item(
        store_id,
        item_code=code,
        name="登山包",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal("1200"),
        acquisition_cost=Decimal("600"),
    )


# ── 必測 1：序號品狀態機 ──
async def test_sell_moves_in_stock_to_sold(db_session: AsyncSession) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    item = await _owned_item(svc, store_id)

    await svc.sell_serialized_item(item.id)
    await db_session.refresh(item)
    assert item.status == SerializedItemStatus.SOLD
    assert item.sold_date is not None


async def test_cannot_sell_twice(db_session: AsyncSession) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    item = await _owned_item(svc, store_id)
    await svc.sell_serialized_item(item.id)
    with pytest.raises(InvalidStateTransition):
        await svc.sell_serialized_item(item.id)


@pytest.mark.parametrize(
    "action",
    ["return_serialized_to_consignor", "write_off_serialized_item"],
)
async def test_legal_transitions_from_in_stock(db_session: AsyncSession, action: str) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    item = await _owned_item(svc, store_id)
    await getattr(svc, action)(item.id)
    await db_session.refresh(item)
    assert item.status != SerializedItemStatus.IN_STOCK


async def test_illegal_transition_after_sold_blocked(db_session: AsyncSession) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    item = await _owned_item(svc, store_id)
    await svc.sell_serialized_item(item.id)
    with pytest.raises(InvalidStateTransition):
        await svc.write_off_serialized_item(item.id)


# ── 必測 2（單機部分）：item_code 唯一、不可重複入庫 ──
async def test_duplicate_item_code_rejected(db_session: AsyncSession) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    await _owned_item(svc, store_id, code="DUP-1")
    with pytest.raises(IntegrityError):
        await _owned_item(svc, store_id, code="DUP-1")


# ── 必測 3：bulk_lot 超賣/歸零/每件成本 ──
async def _bulk_lot(svc: InventoryService, store_id: int, total: int, cost: str) -> BulkLot:
    return await svc.create_bulk_lot(
        store_id,
        lot_code=f"LOT-{total}-{cost}",
        name="散裝雜物",
        grade=Grade.E,
        acquisition_cost=Decimal(cost),
        acquisition_basis=BulkAcquisitionBasis.WEIGHT,
        unit_price=Decimal("50"),
        total_qty=total,
    )


async def test_bulk_oversell_blocked_remaining_not_negative(
    db_session: AsyncSession,
) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    lot = await _bulk_lot(svc, store_id, total=5, cost="1000")

    await svc.sell_bulk_lot_items(lot.id, 3)
    with pytest.raises(InsufficientStock):
        await svc.sell_bulk_lot_items(lot.id, 3)  # 只剩 2，不可賣 3

    await db_session.refresh(lot)
    assert lot.remaining_qty == 2  # 未變負
    assert lot.status == BulkLotStatus.ON_SALE


async def test_bulk_zero_turns_sold_out(db_session: AsyncSession) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    lot = await _bulk_lot(svc, store_id, total=5, cost="1000")
    await svc.sell_bulk_lot_items(lot.id, 5)
    await db_session.refresh(lot)
    assert lot.remaining_qty == 0
    assert lot.status == BulkLotStatus.SOLD_OUT


async def test_bulk_sell_nonpositive_qty_rejected(db_session: AsyncSession) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    lot = await _bulk_lot(svc, store_id, total=5, cost="1000")
    with pytest.raises(InsufficientStock):
        await svc.sell_bulk_lot_items(lot.id, 0)


async def test_bulk_per_piece_cost(db_session: AsyncSession) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    lot = await _bulk_lot(svc, store_id, total=8, cost="1000")
    assert InventoryService.per_piece_cost(lot) == Decimal("125")


# ── 必測 4：ownership / grade 規則 ──
async def test_owned_requires_acquisition_cost(db_session: AsyncSession) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    with pytest.raises(OwnershipValidationError):
        await svc.create_serialized_item(
            store_id,
            item_code="X1",
            name="x",
            grade=Grade.B,
            ownership_type=OwnershipType.OWNED,
            listed_price=Decimal("100"),
        )


async def test_consignment_requires_consignor_and_pct(db_session: AsyncSession) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    with pytest.raises(OwnershipValidationError):
        await svc.create_serialized_item(
            store_id,
            item_code="X2",
            name="x",
            grade=Grade.B,
            ownership_type=OwnershipType.CONSIGNMENT,
            listed_price=Decimal("100"),
        )


async def test_grade_e_cannot_be_serialized(db_session: AsyncSession) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    with pytest.raises(OwnershipValidationError):
        await svc.create_serialized_item(
            store_id,
            item_code="X3",
            name="x",
            grade=Grade.E,
            ownership_type=OwnershipType.OWNED,
            listed_price=Decimal("100"),
            acquisition_cost=Decimal("50"),
        )


async def test_bulk_lot_must_be_grade_e(db_session: AsyncSession) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    with pytest.raises(OwnershipValidationError):
        await svc.create_bulk_lot(
            store_id,
            lot_code="L1",
            name="x",
            grade=Grade.A,
            acquisition_cost=Decimal("1000"),
            acquisition_basis=BulkAcquisitionBasis.BAG,
            unit_price=Decimal("50"),
            total_qty=10,
        )


# ── 必測 5（服務委派）+ 必測 6：主檔唯一 / brand_id / product_model_id ──
def test_suggested_listed_price_delegates_to_money() -> None:
    assert InventoryService.suggested_listed_price(Decimal("600"), 45) == 1091


async def test_brand_get_or_create_unique_per_store_name(
    db_session: AsyncSession,
) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    b1 = await svc.get_or_create_brand(store_id, "Patagonia")
    b2 = await svc.get_or_create_brand(store_id, "Patagonia")
    b3 = await svc.get_or_create_brand(store_id, "Arc'teryx")
    assert b1.id == b2.id
    assert b3.id != b1.id


async def test_product_model_unique_and_item_links_brand_and_model(
    db_session: AsyncSession,
) -> None:
    svc = InventoryService(db_session)
    store_id = await _make_store(db_session)
    brand = await svc.get_or_create_brand(store_id, "Brand")
    m1 = await svc.get_or_create_product_model(store_id, brand.id, "Model-X")
    m2 = await svc.get_or_create_product_model(store_id, brand.id, "Model-X")
    assert m1.id == m2.id

    item = await svc.create_serialized_item(
        store_id,
        item_code="LINK-1",
        name="連結品",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal("900"),
        acquisition_cost=Decimal("500"),
        brand_id=brand.id,
        product_model_id=m1.id,
    )
    assert item.brand_id == brand.id
    assert item.product_model_id == m1.id
