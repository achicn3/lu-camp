"""寄售抽成 × 退貨反轉的報表口徑：反轉後抽成不計入毛利/認列營收（金流問題 #2）。

不變量 #7：退已售寄售品 → 未付結算 CANCELLED、已付結算 reclaim_needed=true。兩者的抽成
在經濟上都不成立（客人已退款），`commission_total_for_sales` 必須排除，否則
margin_breakdown（R2/R6/C4/經營洞察/SC-5b 同源）高估店家收入。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.consignment.service import ConsignmentService
from app.modules.contacts.models import Contact
from app.modules.inventory.service import InventoryService
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    ConsignmentSettlementStatus,
    Grade,
    OwnershipType,
    SaleLineType,
    UserRole,
)


async def _seed(session: AsyncSession) -> tuple[int, int, int]:
    """建 store + clerk + 開帳 + 寄售人；回 (store_id, clerk_id, consignor_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    consignor = Contact(store_id=store.id, name="寄售人甲", roles=["CONSIGNOR"])
    session.add(consignor)
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("5000"))
    return store.id, clerk.id, consignor.id


async def _sell_consignment_item(
    session: AsyncSession, store_id: int, clerk_id: int, consignor_id: int, *, code: str
) -> tuple[int, int]:
    """建寄售序號品（售價 1000、抽成 50%）並售出；回 (sale_id, sale_line_id)。"""
    item = await InventoryService(session).create_serialized_item(
        store_id,
        item_code=code,
        name=f"寄售品-{code}",
        grade=Grade.A,
        ownership_type=OwnershipType.CONSIGNMENT,
        listed_price=Decimal(1000),
        consignor_id=consignor_id,
        commission_pct=50,
    )
    sales = SalesService(session)
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=item.item_code)],
    )
    lines = await sales.get_lines(sale.id)
    return sale.id, lines[0].id


def _window() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return now - timedelta(hours=1), now + timedelta(hours=1)


async def test_cancelled_settlement_commission_excluded(db_session: AsyncSession) -> None:
    """未付結算退貨 → CANCELLED：抽成不再計入 margin_breakdown。"""
    store_id, clerk_id, consignor_id = await _seed(db_session)
    sale_id, line_id = await _sell_consignment_item(
        db_session, store_id, clerk_id, consignor_id, code="CSN-1"
    )
    sales = SalesService(db_session)
    date_from, date_to = _window()

    before = await sales.margin_breakdown(store_id, date_from, date_to)
    assert before.consignment_commission_income == Decimal(500)  # 1000 × 50%
    assert before.gross_margin == Decimal(500)

    await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale_id,
        lines=[ReturnLineInput(sale_line_id=line_id, qty=1)],
        reason="退貨反轉抽成",
        actor_user_id=clerk_id,
        idempotency_key="ret-c1",
    )
    settlement = await db_session.scalar(
        select(ConsignmentSettlement).where(ConsignmentSettlement.sale_id == sale_id)
    )
    assert settlement is not None
    assert settlement.status is ConsignmentSettlementStatus.CANCELLED

    after = await sales.margin_breakdown(store_id, date_from, date_to)
    assert after.consignment_commission_income == Decimal(0)  # 反轉後不計
    assert after.gross_margin == Decimal(0)
    # period_margin（SC-5b 引擎）同源同口徑。
    pm = await sales.period_margin(store_id, date_from, date_to)
    assert pm["consignment_commission"] == Decimal(0)


async def test_reclaim_needed_settlement_commission_excluded(db_session: AsyncSession) -> None:
    """已付結算退貨 → PAID + reclaim_needed：抽成同樣不計（款待向寄售人追回）。"""
    store_id, clerk_id, consignor_id = await _seed(db_session)
    sale_id, line_id = await _sell_consignment_item(
        db_session, store_id, clerk_id, consignor_id, code="CSN-2"
    )
    settlement = await db_session.scalar(
        select(ConsignmentSettlement).where(ConsignmentSettlement.sale_id == sale_id)
    )
    assert settlement is not None
    await ConsignmentService(db_session).pay_settlement(
        store_id, settlement.id, actor_user_id=clerk_id, idempotency_key="pay-c2"
    )

    await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale_id,
        lines=[ReturnLineInput(sale_line_id=line_id, qty=1)],
        reason="已付退貨",
        actor_user_id=clerk_id,
        idempotency_key="ret-c2",
    )
    await db_session.refresh(settlement)
    assert settlement.status is ConsignmentSettlementStatus.PAID
    assert settlement.reclaim_needed is True

    date_from, date_to = _window()
    after = await SalesService(db_session).margin_breakdown(store_id, date_from, date_to)
    assert after.consignment_commission_income == Decimal(0)


async def test_active_settlements_still_counted(db_session: AsyncSession) -> None:
    """未退貨的 PENDING 與 PAID（未 reclaim）抽成照常計入（不誤殺）。"""
    store_id, clerk_id, consignor_id = await _seed(db_session)
    sale1, _ = await _sell_consignment_item(
        db_session, store_id, clerk_id, consignor_id, code="CSN-3"
    )
    sale2, _ = await _sell_consignment_item(
        db_session, store_id, clerk_id, consignor_id, code="CSN-4"
    )
    s2 = await db_session.scalar(
        select(ConsignmentSettlement).where(ConsignmentSettlement.sale_id == sale2)
    )
    assert s2 is not None
    await ConsignmentService(db_session).pay_settlement(
        store_id, s2.id, actor_user_id=clerk_id, idempotency_key="pay-c4"
    )

    date_from, date_to = _window()
    bd = await SalesService(db_session).margin_breakdown(store_id, date_from, date_to)
    assert bd.consignment_commission_income == Decimal(1000)  # 500 + 500
    _ = sale1
