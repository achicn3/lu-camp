"""餐飲損耗（docs/49 F2/F3）：報廢、盤點短少、客訴退款三欄，成本從餐飲毛利與全店毛利扣除。

- 報廢／盤點短少的成本在按下當下凍結（之後改成本不改寫）。
- 客訴退款（沒勾「還能賣」）：營收扣回，成本從「賣出成本」移到「損耗」——不重複扣。
- 成本未知的損耗只計份數。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.menu.models import MenuStockAdjustment
from app.modules.menu.service import MenuService
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.models import SaleLine
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole
from tests.integration.test_returns_menu import _mixed_sale


def _window() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    return now - timedelta(hours=1), now + timedelta(hours=1)


async def _cake(session: AsyncSession, cost: Decimal | None) -> tuple[int, int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="m", password_hash="h", role=UserRole.MANAGER)
    session.add(mgr)
    await session.flush()
    menu = MenuService(session)
    cake = await menu.create_menu_item(
        store.id, name="戚風", unit_price=Decimal(90), unit_cost=cost, actor_user_id=mgr.id
    )
    await menu.update_menu_item(store.id, cake.id, daily_limited=True, actor_user_id=mgr.id)
    await menu.set_daily_stock(
        store.id, "item", cake.id, qty=5, expected_remaining=0, actor_user_id=mgr.id
    )
    return store.id, mgr.id, cake.id


async def test_waste_and_shortage_cost_reduce_food_margin(db_session: AsyncSession) -> None:
    store_id, uid, cake = await _cake(db_session, Decimal(40))
    menu = MenuService(db_session)
    sales = SalesService(db_session)
    t0, t1 = _window()
    before = await sales.margin_breakdown(store_id, t0, t1)

    await menu.adjust_daily_stock(
        store_id, "item", cake, delta=-2, reason="WASTE", actor_user_id=uid
    )
    await menu.update_menu_item(store_id, cake, unit_cost=Decimal(55), actor_user_id=uid)
    await menu.adjust_daily_stock(
        store_id, "item", cake, delta=-1, reason="CORRECTION", actor_user_id=uid
    )
    snaps = (
        await db_session.scalars(
            select(MenuStockAdjustment.unit_cost_snapshot).order_by(MenuStockAdjustment.id)
        )
    ).all()
    assert list(snaps) == [Decimal(40), Decimal(55)]  # 按下當下的成本，之後改成本不改寫

    after = await sales.margin_breakdown(store_id, t0, t1)
    assert after.food_waste_cost == Decimal(135)  # 2×40 + 1×55
    assert before.food_margin - after.food_margin == Decimal(135)
    assert before.gross_margin - after.gross_margin == Decimal(135)
    rows = {r.reason: (r.qty, r.cost, r.unknown_cost_qty) for r in after.food_waste_breakdown}
    assert rows["WASTE"] == (2, Decimal(80), 0)
    assert rows["SHORTAGE"] == (1, Decimal(55), 0)


async def test_waste_with_unknown_cost_counts_qty_only(db_session: AsyncSession) -> None:
    store_id, uid, cake = await _cake(db_session, None)
    await MenuService(db_session).adjust_daily_stock(
        store_id, "item", cake, delta=-1, reason="WASTE", actor_user_id=uid
    )
    t0, t1 = _window()
    after = await SalesService(db_session).margin_breakdown(store_id, t0, t1)
    assert after.food_waste_cost == Decimal(0)
    rows = {r.reason: (r.qty, r.cost, r.unknown_cost_qty) for r in after.food_waste_breakdown}
    assert rows["WASTE"] == (1, Decimal(0), 1)


async def test_restock_is_not_waste(db_session: AsyncSession) -> None:
    store_id, uid, cake = await _cake(db_session, Decimal(40))
    await MenuService(db_session).adjust_daily_stock(
        store_id, "item", cake, delta=3, actor_user_id=uid
    )
    t0, t1 = _window()
    after = await SalesService(db_session).margin_breakdown(store_id, t0, t1)
    assert after.food_waste_cost == Decimal(0)


async def test_unresellable_refund_cost_moves_to_waste_not_double_counted(
    db_session: AsyncSession,
) -> None:
    """客人吃壞退款：營收扣 150、賣出成本扣 45、損耗加 45 → 毛利少 150（材料錢真的花掉了）。"""
    m = await _mixed_sale(db_session)
    line = await db_session.get(SaleLine, m.latte_line)
    assert line is not None
    line.cost_snapshot = Decimal(90)
    await db_session.flush()
    t0, t1 = _window()
    sales = SalesService(db_session)
    before = await sales.margin_breakdown(m.store_id, t0, t1)
    await ReturnsService(db_session).create_return(
        m.store_id,
        sale_id=m.sale_id,
        lines=[ReturnLineInput(m.latte_line, 1)],
        reason="吃到壞的",
        actor_user_id=m.clerk_id,
        idempotency_key="bad",
    )
    after = await sales.margin_breakdown(m.store_id, t0, t1)
    assert before.food_revenue - after.food_revenue == Decimal(150)
    assert before.food_cogs - after.food_cogs == Decimal(45)
    assert after.food_waste_cost - before.food_waste_cost == Decimal(45)
    assert before.food_margin - after.food_margin == Decimal(150)
    rows = {r.reason: (r.qty, r.cost) for r in after.food_waste_breakdown}
    assert rows["REFUND"] == (1, Decimal(45))


async def test_resellable_flag_is_persisted_on_return_line(db_session: AsyncSession) -> None:
    from app.modules.returns.models import ReturnLine

    m = await _mixed_sale(db_session)
    await ReturnsService(db_session).create_return(
        m.store_id,
        sale_id=m.sale_id,
        lines=[ReturnLineInput(m.latte_line, 1, resellable=True)],
        reason="還沒做就取消",
        actor_user_id=m.clerk_id,
        idempotency_key="keep",
    )
    flags = (await db_session.scalars(select(ReturnLine.resellable))).all()
    assert list(flags) == [True]


async def test_sales_margin_report_exposes_food_waste(db_session: AsyncSession) -> None:
    from app.modules.reports.service import ReportsService

    store_id, uid, cake = await _cake(db_session, Decimal(40))
    await MenuService(db_session).adjust_daily_stock(
        store_id, "item", cake, delta=-2, reason="WASTE", actor_user_id=uid
    )
    t0, t1 = _window()
    report = await ReportsService(db_session).sales_margin(store_id, date_from=t0, date_to=t1)
    assert report.food_waste_cost == Decimal(80)
    rows = {r.reason: (r.qty, r.cost) for r in report.food_waste_breakdown}
    assert rows == {
        "WASTE": (2, Decimal(80)),
        "SHORTAGE": (0, Decimal(0)),
        "REFUND": (0, Decimal(0)),
    }
