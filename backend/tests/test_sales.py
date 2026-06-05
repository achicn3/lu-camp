"""T11 — sales 領域層：混合購物車、扣庫存、收現、推稅、寄售結算、發票開關。

對齊 CLAUDE.md §6/§7。原子性與併發另見 integration/test_sales_atomic.py、
integration/test_sales_concurrency.py。
"""

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import commission, split_tax_inclusive
from app.modules.cashdrawer.models import CashMovement
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.contacts.models import Contact
from app.modules.inventory.models import BulkLot, CatalogProduct, SerializedItem, StockMovement
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    CashMovementType,
    ConsignmentSettlementStatus,
    Grade,
    OwnershipType,
    SaleInvoiceStatus,
    SaleLineType,
    SerializedItemStatus,
    StockDirection,
    UserRole,
)

TAX_RATE = Decimal("0.05")


async def _seed_base(session: AsyncSession, *, open_drawer: bool = True) -> tuple[int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    if open_drawer:
        await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    return store.id, clerk.id


async def _seed_serialized(
    session: AsyncSession,
    store_id: int,
    *,
    code: str,
    price: Decimal,
    ownership: OwnershipType,
    consignor_id: int | None = None,
    commission_pct: int | None = None,
) -> SerializedItem:
    inv = InventoryService(session)
    return await inv.create_serialized_item(
        store_id,
        item_code=code,
        name=f"序號品-{code}",
        grade=Grade.A,
        ownership_type=ownership,
        listed_price=price,
        acquisition_cost=Decimal("100") if ownership == OwnershipType.OWNED else None,
        consignor_id=consignor_id,
        commission_pct=commission_pct,
    )


async def _seed_catalog(
    session: AsyncSession, store_id: int, *, price: Decimal, qty: int
) -> CatalogProduct:
    product = CatalogProduct(
        store_id=store_id, sku="SKU1", name="飲料", unit_price=price, quantity_on_hand=qty
    )
    session.add(product)
    await session.flush()
    return product


async def _seed_bulk(
    session: AsyncSession, store_id: int, *, price: Decimal, total_qty: int
) -> BulkLot:
    return await InventoryService(session).create_bulk_lot(
        store_id,
        lot_code="L1",
        name="散裝零件",
        grade=Grade.E,
        acquisition_cost=Decimal("100"),
        acquisition_basis=BulkAcquisitionBasis.BAG,
        unit_price=price,
        total_qty=total_qty,
    )


async def _count(session: AsyncSession, model: Any, store_id: int) -> int:
    n = await session.scalar(
        select(func.count()).select_from(model).where(model.store_id == store_id)
    )
    return n or 0


async def test_mixed_cart_serialized_catalog_bulk(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed_base(db_session)
    ser = await _seed_serialized(
        db_session, store_id, code="S1", price=Decimal("3000"), ownership=OwnershipType.OWNED
    )
    cat = await _seed_catalog(db_session, store_id, price=Decimal("150"), qty=10)
    lot = await _seed_bulk(db_session, store_id, price=Decimal("50"), total_qty=10)

    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="S1"),
            SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=cat.id, qty=2),
            SaleLineInput(line_type=SaleLineType.BULK_LOT, bulk_lot_id=lot.id, qty=3),
        ],
    )

    # 金額：3000 + 150*2 + 50*3 = 3450；稅於總額層級推一次。
    total = Decimal("3450")
    net, tax = split_tax_inclusive(total, TAX_RATE)
    assert sale.total == total
    assert sale.subtotal == net
    assert sale.tax == tax
    assert sale.subtotal + sale.tax == sale.total
    assert sale.invoice_status == SaleInvoiceStatus.NOT_ISSUED

    # 三行明細、三筆 OUT 異動。
    assert await _count(db_session, StockMovement, store_id) == 3
    directions = (
        await db_session.scalars(
            select(StockMovement.direction).where(StockMovement.store_id == store_id)
        )
    ).all()
    assert set(directions) == {StockDirection.OUT}

    # 庫存正確扣減。
    await db_session.refresh(ser)
    await db_session.refresh(cat)
    await db_session.refresh(lot)
    assert ser.status == SerializedItemStatus.SOLD
    assert cat.quantity_on_hand == 8  # 10 - 2
    assert lot.remaining_qty == 7  # 10 - 3

    # 收現 SALE_IN = 總額。
    movement = await db_session.scalar(
        select(CashMovement).where(
            CashMovement.store_id == store_id, CashMovement.type == CashMovementType.SALE_IN
        )
    )
    assert movement is not None
    assert movement.amount == total

    # 買斷品不建寄售結算。
    assert await _count(db_session, ConsignmentSettlement, store_id) == 0


async def test_bulk_zero_turns_sold_out(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed_base(db_session)
    lot = await _seed_bulk(db_session, store_id, price=Decimal("50"), total_qty=3)
    await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.BULK_LOT, bulk_lot_id=lot.id, qty=3)],
    )
    await db_session.refresh(lot)
    assert lot.remaining_qty == 0
    assert lot.status == BulkLotStatus.SOLD_OUT


async def test_consignment_sale_creates_pending_settlement_store_takes_commission_only(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id = await _seed_base(db_session)
    consignor = Contact(store_id=store_id, name="寄售人", national_id_enc="enc")
    db_session.add(consignor)
    await db_session.flush()
    ser = await _seed_serialized(
        db_session,
        store_id,
        code="C1",
        price=Decimal("2000"),
        ownership=OwnershipType.CONSIGNMENT,
        consignor_id=consignor.id,
        commission_pct=50,
    )

    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="C1")],
    )

    settlement = await db_session.scalar(
        select(ConsignmentSettlement).where(ConsignmentSettlement.store_id == store_id)
    )
    assert settlement is not None
    assert settlement.sale_id == sale.id
    assert settlement.serialized_item_id == ser.id
    assert settlement.status == ConsignmentSettlementStatus.PENDING
    assert settlement.gross == Decimal("2000")
    # 店家收入只認抽成；應付寄售人 = 售價 − 抽成。
    expected_commission = commission(Decimal("2000"), 50)
    assert settlement.commission_amount == Decimal(expected_commission)  # 1000
    assert settlement.payout_amount == Decimal("2000") - Decimal(expected_commission)  # 1000
    assert settlement.commission_amount + settlement.payout_amount == settlement.gross


async def test_no_open_session_blocks_entire_sale(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed_base(db_session, open_drawer=False)
    cat = await _seed_catalog(db_session, store_id, price=Decimal("150"), qty=10)
    from app.shared.exceptions import NoOpenCashSession

    with pytest.raises(NoOpenCashSession):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=cat.id, qty=1)],
        )
    # 未動任何庫存/銷售/異動。
    await db_session.refresh(cat)
    assert cat.quantity_on_hand == 10
    assert await _count(db_session, StockMovement, store_id) == 0


async def test_resell_sold_serialized_raises(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed_base(db_session)
    await _seed_serialized(
        db_session, store_id, code="S1", price=Decimal("3000"), ownership=OwnershipType.OWNED
    )
    svc = SalesService(db_session)
    await svc.create_sale(
        store_id, clerk_id, lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="S1")]
    )
    from app.shared.exceptions import InvalidStateTransition

    with pytest.raises(InvalidStateTransition):
        await svc.create_sale(
            store_id,
            clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="S1")],
        )


async def test_insufficient_bulk_stock_raises(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed_base(db_session)
    lot = await _seed_bulk(db_session, store_id, price=Decimal("50"), total_qty=2)
    from app.shared.exceptions import InsufficientStock

    with pytest.raises(InsufficientStock):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.BULK_LOT, bulk_lot_id=lot.id, qty=5)],
        )


async def test_empty_sale_raises(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed_base(db_session)
    from app.shared.exceptions import EmptySale

    with pytest.raises(EmptySale):
        await SalesService(db_session).create_sale(store_id, clerk_id, lines=[])


async def test_item_not_found_raises(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed_base(db_session)
    from app.shared.exceptions import SaleItemNotFound

    with pytest.raises(SaleItemNotFound):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="NOPE")],
        )


async def test_line_shape_invalid_raises(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed_base(db_session)
    from app.shared.exceptions import SaleLineInvalid

    with pytest.raises(SaleLineInvalid):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED)],  # 缺 item_code
        )


async def test_catalog_line_validations(db_session: AsyncSession) -> None:
    from app.shared.exceptions import InsufficientStock, SaleItemNotFound, SaleLineInvalid

    store_id, clerk_id = await _seed_base(db_session)
    svc = SalesService(db_session)
    # 缺 catalog_product_id。
    with pytest.raises(SaleLineInvalid):
        await svc.create_sale(
            store_id, clerk_id, lines=[SaleLineInput(line_type=SaleLineType.CATALOG, qty=1)]
        )
    # 數量 <= 0。
    with pytest.raises(SaleLineInvalid):
        await svc.create_sale(
            store_id,
            clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=1, qty=0)],
        )
    # 找不到商品。
    with pytest.raises(SaleItemNotFound):
        await svc.create_sale(
            store_id,
            clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=999999, qty=1)],
        )
    # 庫存不足。
    cat = await _seed_catalog(db_session, store_id, price=Decimal("150"), qty=1)
    with pytest.raises(InsufficientStock):
        await svc.create_sale(
            store_id,
            clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=cat.id, qty=5)],
        )


async def test_bulk_line_validations(db_session: AsyncSession) -> None:
    from app.shared.exceptions import SaleItemNotFound, SaleLineInvalid

    store_id, clerk_id = await _seed_base(db_session)
    svc = SalesService(db_session)
    # 缺 bulk_lot_id。
    with pytest.raises(SaleLineInvalid):
        await svc.create_sale(
            store_id, clerk_id, lines=[SaleLineInput(line_type=SaleLineType.BULK_LOT, qty=1)]
        )
    # 數量 <= 0。
    with pytest.raises(SaleLineInvalid):
        await svc.create_sale(
            store_id,
            clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.BULK_LOT, bulk_lot_id=1, qty=0)],
        )
    # 找不到散裝批。
    with pytest.raises(SaleItemNotFound):
        await svc.create_sale(
            store_id,
            clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.BULK_LOT, bulk_lot_id=999999, qty=1)],
        )


async def test_consignment_item_missing_commission_pct_raises(db_session: AsyncSession) -> None:
    # 防呆：寄售品資料異常缺 commission_pct（直接建異常列繞過 inventory 驗證）→ 結帳擋下。
    from app.shared.exceptions import SaleLineInvalid

    store_id, clerk_id = await _seed_base(db_session)
    bad = SerializedItem(
        store_id=store_id,
        item_code="BAD-CON",
        name="異常寄售品",
        grade=Grade.A,
        ownership_type=OwnershipType.CONSIGNMENT,
        listed_price=Decimal("1000"),
        consignor_id=None,
        commission_pct=None,
    )
    db_session.add(bad)
    await db_session.flush()
    with pytest.raises(SaleLineInvalid):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code="BAD-CON")],
        )


async def test_cross_store_buyer_contact_blocked(db_session: AsyncSession) -> None:
    from app.shared.exceptions import CrossStoreReference

    store_id, clerk_id = await _seed_base(db_session)
    other = Store(name="他店")
    db_session.add(other)
    await db_session.flush()
    other_contact = Contact(store_id=other.id, name="他店客", national_id_enc="enc")
    db_session.add(other_contact)
    await db_session.flush()
    cat = await _seed_catalog(db_session, store_id, price=Decimal("150"), qty=5)

    with pytest.raises(CrossStoreReference):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=cat.id, qty=1)],
            buyer_contact_id=other_contact.id,
        )
    # 跨店被擋在動庫存之前。
    await db_session.refresh(cat)
    assert cat.quantity_on_hand == 5


async def test_cross_store_clerk_blocked(db_session: AsyncSession) -> None:
    from app.shared.exceptions import CrossStoreReference

    store_id, _ = await _seed_base(db_session)
    other = Store(name="他店")
    db_session.add(other)
    await db_session.flush()
    other_clerk = User(
        store_id=other.id, username="other-clk", password_hash="h", role=UserRole.CLERK
    )
    db_session.add(other_clerk)
    await db_session.flush()
    cat = await _seed_catalog(db_session, store_id, price=Decimal("150"), qty=5)

    with pytest.raises(CrossStoreReference):
        await SalesService(db_session).create_sale(
            store_id,
            other_clerk.id,  # 他店店員
            lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=cat.id, qty=1)],
        )


async def test_same_store_buyer_contact_ok(db_session: AsyncSession) -> None:
    store_id, clerk_id = await _seed_base(db_session)
    buyer = Contact(store_id=store_id, name="本店客", national_id_enc="enc")
    db_session.add(buyer)
    await db_session.flush()
    cat = await _seed_catalog(db_session, store_id, price=Decimal("150"), qty=5)
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=cat.id, qty=1)],
        buyer_contact_id=buyer.id,
    )
    assert sale.buyer_contact_id == buyer.id


async def test_einvoice_disabled_sale_still_fully_recorded(db_session: AsyncSession) -> None:
    # 預設 einvoice_enabled=false → invoice_status=NOT_ISSUED，但 sale/明細/異動皆完整。
    store_id, clerk_id = await _seed_base(db_session)
    cat = await _seed_catalog(db_session, store_id, price=Decimal("105"), qty=5)
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=cat.id, qty=1)],
    )
    assert sale.invoice_status == SaleInvoiceStatus.NOT_ISSUED
    assert sale.total == Decimal("105")
    net, tax = split_tax_inclusive(Decimal("105"), TAX_RATE)
    assert sale.subtotal == net  # 100
    assert sale.tax == tax  # 5
    assert await _count(db_session, StockMovement, store_id) == 1
