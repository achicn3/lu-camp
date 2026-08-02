"""結帳時的臨時折扣（P2-3）：折扣落到明細、分攤留盤、金額三方一致。

關鍵不變量：**Σ net_amount == sale.total == Σ tenders**。
發票品項的加總必須等於發票總額（amego 有硬檢查），退貨也依 net_amount 退實付——
所以折扣一定要落到明細，不能只掛在訂單層。
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import CatalogProduct
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.models import SaleAdjustment, SaleAdjustmentAllocation, SaleLine
from app.modules.sales.pricing import DiscountRequest
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    AdjustmentScope,
    CalculationMethod,
    SaleLineType,
    TenderType,
    UserRole,
)
from app.shared.exceptions import InvalidDiscount


async def _seed(session: AsyncSession) -> tuple[int, int, int, int]:
    store = Store(name="折扣店", tax_id="12345678")
    session.add(store)
    await session.flush()
    clerk = User(
        store_id=store.id, username=f"d-{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    session.add(clerk)
    session.add(StoreSettings(store_id=store.id))
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("5000"))
    a = CatalogProduct(
        store_id=store.id, sku=f"A-{store.id}", name="甲", unit_price=Decimal("600"),
        unit_cost=Decimal("200"), quantity_on_hand=50,
    )
    b = CatalogProduct(
        store_id=store.id, sku=f"B-{store.id}", name="乙", unit_price=Decimal("400"),
        unit_cost=Decimal("150"), quantity_on_hand=50,
    )
    session.add_all([a, b])
    await session.flush()
    return store.id, clerk.id, a.id, b.id


def _line(pid: int, qty: int = 1) -> SaleLineInput:
    return SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=pid, qty=qty)


async def _lines_of(session: AsyncSession, sale_id: int) -> list[SaleLine]:
    return list(
        (
            await session.scalars(
                select(SaleLine).where(SaleLine.sale_id == sale_id).order_by(SaleLine.id)
            )
        ).all()
    )


async def test_item_discount_reduces_the_line_and_the_payable(db_session: AsyncSession) -> None:
    store_id, clerk_id, a_id, b_id = await _seed(db_session)
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[_line(a_id), _line(b_id)],
        adjustments=[
            DiscountRequest(
                AdjustmentScope.ITEM, CalculationMethod.FIXED_AMOUNT, Decimal(100), target_key="0"
            )
        ],
    )
    assert sale.total == Decimal(900)  # 600−100 + 400
    la, lb = await _lines_of(db_session, sale.id)
    assert (la.line_total, la.manual_discount_amount, la.net_amount) == (
        Decimal(600), Decimal(100), Decimal(500),
    )
    assert (lb.manual_discount_amount, lb.net_amount) == (Decimal(0), Decimal(400))
    # unit_price 不被覆蓋——牌價與折扣分開留痕
    assert la.unit_price == Decimal(600)


async def test_order_discount_is_allocated_and_persisted(db_session: AsyncSession) -> None:
    """整單折扣必須留下**分攤結果**，退貨才知道各行當初被折了多少。"""
    store_id, clerk_id, a_id, b_id = await _seed(db_session)
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[_line(a_id), _line(b_id)],
        adjustments=[
            DiscountRequest(AdjustmentScope.ORDER, CalculationMethod.FIXED_AMOUNT, Decimal(100))
        ],
    )
    assert sale.total == Decimal(900)
    la, lb = await _lines_of(db_session, sale.id)
    assert (la.manual_discount_amount, la.net_amount) == (Decimal(60), Decimal(540))
    assert (lb.manual_discount_amount, lb.net_amount) == (Decimal(40), Decimal(360))

    adj = await db_session.scalar(
        select(SaleAdjustment).where(SaleAdjustment.sale_id == sale.id)
    )
    assert adj is not None
    assert adj.scope is AdjustmentScope.ORDER
    assert adj.applied_amount == Decimal(100)
    assert adj.sale_line_id is None
    assert adj.created_by == clerk_id

    allocs = (
        await db_session.scalars(
            select(SaleAdjustmentAllocation)
            .where(SaleAdjustmentAllocation.adjustment_id == adj.id)
            .order_by(SaleAdjustmentAllocation.sale_line_id)
        )
    ).all()
    assert [(x.sale_line_id, x.allocated_amount) for x in allocs] == [
        (la.id, Decimal(60)),
        (lb.id, Decimal(40)),
    ]


async def test_payable_matches_lines_and_tenders(db_session: AsyncSession) -> None:
    """三方一致：Σ net_amount == sale.total == Σ tenders。

    這條若破，發票會因「品項小計合計 ≠ 發票總額」被平台拒送，且永遠卡住。
    """
    store_id, clerk_id, a_id, b_id = await _seed(db_session)
    sales = SalesService(db_session)
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[_line(a_id), _line(b_id)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(880))],
        adjustments=[
            DiscountRequest(AdjustmentScope.ORDER, CalculationMethod.PERCENTAGE, Decimal(12))
        ],
    )
    lines = await _lines_of(db_session, sale.id)
    tenders = await sales.get_tenders(sale.id)
    assert sum(line.net_amount for line in lines) == sale.total
    assert sum(t.amount for t in tenders) == sale.total
    assert sale.total == Decimal(880)  # 1000 的 12% = 120


async def test_gifts_are_excluded_from_order_discount_allocation(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id, a_id, b_id = await _seed(db_session)
    from app.modules.sales.models import GiftReason

    reason = GiftReason(store_id=store_id, code="PROMO", name="活動贈品")
    db_session.add(reason)
    await db_session.flush()

    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[
            _line(a_id),
            SaleLineInput(
                line_type=SaleLineType.CATALOG,
                catalog_product_id=b_id,
                qty=1,
                line_kind=__import__(
                    "app.shared.enums", fromlist=["SaleLineKind"]
                ).SaleLineKind.GIFT,
                gift_reason_id=reason.id,
            ),
        ],
        adjustments=[
            DiscountRequest(AdjustmentScope.ORDER, CalculationMethod.FIXED_AMOUNT, Decimal(60))
        ],
    )
    la, gift = await _lines_of(db_session, sale.id)
    assert la.manual_discount_amount == Decimal(60)  # 整筆落在唯一可折的行
    assert gift.manual_discount_amount == Decimal(0)
    assert sale.total == Decimal(540)


async def test_discount_that_would_zero_the_order_is_rejected(db_session: AsyncSession) -> None:
    """折到 0＝變相贈品，要免費請用贈品（否則贈品報表統計不到）。"""
    store_id, clerk_id, a_id, _b_id = await _seed(db_session)
    with pytest.raises(InvalidDiscount, match="贈品"):
        await SalesService(db_session).create_sale(
            store_id,
            clerk_id,
            lines=[_line(a_id)],
            adjustments=[
                DiscountRequest(
                    AdjustmentScope.ORDER, CalculationMethod.FIXED_AMOUNT, Decimal(600)
                )
            ],
        )


async def test_same_key_with_different_discount_is_not_replayed(
    db_session: AsyncSession,
) -> None:
    """冪等指紋必須含折扣：否則兩張金額不同的單會被當成同一張重放。"""
    from app.shared.exceptions import IdempotencyKeyConflict

    store_id, clerk_id, a_id, _b_id = await _seed(db_session)
    sales = SalesService(db_session)
    await sales.create_sale(
        store_id,
        clerk_id,
        lines=[_line(a_id)],
        idempotency_key="dup-1",
        adjustments=[
            DiscountRequest(
                AdjustmentScope.ITEM, CalculationMethod.FIXED_AMOUNT, Decimal(50), target_key="0"
            )
        ],
    )
    with pytest.raises(IdempotencyKeyConflict):
        await sales.create_sale(
            store_id,
            clerk_id,
            lines=[_line(a_id)],
            idempotency_key="dup-1",
            adjustments=[
                DiscountRequest(
                    AdjustmentScope.ITEM,
                    CalculationMethod.FIXED_AMOUNT,
                    Decimal(200),
                    target_key="0",
                )
            ],
        )


async def test_discount_and_gift_are_audited(db_session: AsyncSession) -> None:
    """裁量性的價格變動必須留痕——這是唯一能事後追究的東西（無主管核准機制）。"""
    from app.core.audit import AuditLog

    store_id, clerk_id, a_id, _b_id = await _seed(db_session)
    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[_line(a_id)],
        adjustments=[
            DiscountRequest(
                AdjustmentScope.ITEM, CalculationMethod.PERCENTAGE, Decimal(10), target_key="0"
            )
        ],
    )
    log = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "SALE_MANUAL_ADJUSTMENT",
            AuditLog.entity_id == str(sale.id),
        )
    )
    assert log is not None
    assert log.actor_user_id == clerk_id
    assert log.after is not None
    assert log.after["total_discount_amount"] == "60"
