"""選項成本（docs/49 F1）：選項可填成本，結帳時與品項成本一起凍結進明細。

- 一份成本＝品項成本＋Σ 所選選項成本；選項沒填視為 0（冰／熱這類沒有額外材料）。
- 品項沒填成本＝整份未知（不假裝是 0，否則報表以為毛利 100%）。
- 改選項成本寫稽核；之後改不影響已成交的明細。
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.menu.service import MenuService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import SaleLineType, ServiceMode, UserRole
from app.shared.exceptions import SaleLineInvalid


async def _ctx(
    session: AsyncSession, *, item_cost: Decimal | None
) -> tuple[int, int, int, dict[str, int]]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="m", password_hash="h", role=UserRole.MANAGER)
    session.add(mgr)
    await session.flush()
    await CashDrawerService(session).open_session(store.id, mgr.id, Decimal(1000))
    svc = MenuService(session)
    latte = await svc.create_menu_item(
        store.id, name="拿鐵", unit_price=Decimal(150), unit_cost=item_cost, actor_user_id=mgr.id
    )
    milk = await svc.create_option_group(
        store.id,
        name="奶",
        min_select=1,
        max_select=1,
        options=[("鮮奶", Decimal(0)), ("燕麥奶", Decimal(20))],
        actor_user_id=mgr.id,
    )
    await svc.set_item_option_groups(store.id, latte.id, [milk.group.id], actor_user_id=mgr.id)
    fresh, oat = milk.options
    return store.id, mgr.id, latte.id, {"鮮奶": fresh.id, "燕麥奶": oat.id}


async def _sell(
    session: AsyncSession, store_id: int, uid: int, item_id: int, option_id: int, qty: int, key: str
) -> Decimal | None:
    sale = await SalesService(session).create_sale(
        store_id,
        uid,
        lines=[
            SaleLineInput(
                line_type=SaleLineType.MENU,
                menu_item_id=item_id,
                qty=qty,
                menu_option_ids=(option_id,),
            )
        ],
        idempotency_key=key,
        service_mode=ServiceMode.TAKEOUT,
    )
    [line] = await SalesService(session).get_lines(sale.id)
    return line.cost_snapshot


async def test_option_cost_is_added_and_frozen(db_session: AsyncSession) -> None:
    store_id, uid, latte, opt = await _ctx(db_session, item_cost=Decimal(40))
    svc = MenuService(db_session)
    updated = await svc.update_option(
        store_id, opt["燕麥奶"], unit_cost=Decimal(8), actor_user_id=uid
    )
    assert updated.unit_cost == Decimal(8)
    assert await _sell(db_session, store_id, uid, latte, opt["燕麥奶"], 2, "c1") == Decimal(96)
    # 選項沒填成本視為 0
    assert await _sell(db_session, store_id, uid, latte, opt["鮮奶"], 1, "c2") == Decimal(40)
    # 之後改成本不改寫已成交
    await svc.update_option(store_id, opt["燕麥奶"], unit_cost=Decimal(12), actor_user_id=uid)
    assert await _sell(db_session, store_id, uid, latte, opt["燕麥奶"], 1, "c3") == Decimal(52)


async def test_unknown_item_cost_stays_unknown_even_with_option_cost(
    db_session: AsyncSession,
) -> None:
    store_id, uid, latte, opt = await _ctx(db_session, item_cost=None)
    await MenuService(db_session).update_option(
        store_id, opt["燕麥奶"], unit_cost=Decimal(8), actor_user_id=uid
    )
    assert await _sell(db_session, store_id, uid, latte, opt["燕麥奶"], 1, "u1") is None


async def test_option_cost_change_is_audited_and_can_be_cleared(db_session: AsyncSession) -> None:
    store_id, uid, _, opt = await _ctx(db_session, item_cost=Decimal(40))
    svc = MenuService(db_session)
    await svc.update_option(store_id, opt["燕麥奶"], unit_cost=Decimal(8), actor_user_id=uid)
    cleared = await svc.update_option(store_id, opt["燕麥奶"], unit_cost=None, actor_user_id=uid)
    assert cleared.unit_cost is None
    logs = (
        await db_session.scalars(
            select(AuditLog).where(AuditLog.action == "UPDATE_MENU_OPTION_COST")
        )
    ).all()
    assert [(log.before, log.after) for log in logs] == [
        ({"unit_cost": None}, {"unit_cost": "8"}),
        ({"unit_cost": "8"}, {"unit_cost": None}),
    ]


@pytest.mark.parametrize("cost", [Decimal(-1), Decimal("2.5")])
async def test_option_cost_must_be_whole_nonnegative(
    db_session: AsyncSession, cost: Decimal
) -> None:
    store_id, uid, _, opt = await _ctx(db_session, item_cost=Decimal(40))
    with pytest.raises(SaleLineInvalid):
        await MenuService(db_session).update_option(
            store_id, opt["燕麥奶"], unit_cost=cost, actor_user_id=uid
        )
