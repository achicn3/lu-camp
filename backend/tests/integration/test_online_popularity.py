"""線上點餐的人氣標籤（docs/63 §7 M2b；店主 2026-10-10 裁示）。

只用 POS 真實成交：餐飲品項近 N 天（台北日，預設 30）淨銷量＝成交份數 − 退貨份數，作廢的不算；
每個分類前三名、淨銷量達門檻（預設 10 份）才上榜：第一名「人氣 No.1」、二三名「人氣推薦」。
後台可開關、改天數與門檻（管理者，寫稽核）。人氣跟著可售狀態同步推上雲端，不必重新發佈。
"""

from collections.abc import AsyncGenerator
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.core.time import utc_now
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.menu.models import MenuCategory, MenuItem
from app.modules.onlineorder.availability_service import AvailabilityService
from app.modules.onlineorder.popularity_service import PopularityService
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.models import Sale, SaleLine
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import SaleLineType, ServiceMode, TenderType, UserRole
from tests.integration.test_online_availability import _publish

URL = "/api/v1/online-order/popularity"


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


class Ctx:
    def __init__(self, store_id: int, manager_id: int, clerk: str, manager: str) -> None:
        self.store_id = store_id
        self.manager_id = manager_id
        self.clerk = clerk
        self.manager = manager
        self.items: dict[str, int] = {}


async def _seed(session: AsyncSession) -> Ctx:
    store = Store(name="人氣店")
    session.add(store)
    await session.flush()
    manager = User(
        store_id=store.id, username=f"pop-m-{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    clerk = User(
        store_id=store.id, username=f"pop-c-{store.id}", password_hash="h", role=UserRole.CLERK
    )
    session.add_all([manager, clerk, StoreSettings(store_id=store.id)])
    await session.flush()
    await CashDrawerService(session).open_session(store.id, manager.id, Decimal("5000"))
    ctx = Ctx(
        store.id,
        manager.id,
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=manager.id, role="MANAGER", store_id=store.id),
    )
    coffee = MenuCategory(store_id=store.id, name="咖啡", sort_order=0)
    dessert = MenuCategory(store_id=store.id, name="甜點", sort_order=1)
    session.add_all([coffee, dessert])
    await session.flush()
    for order, (name, category) in enumerate(
        [
            ("拿鐵", coffee),
            ("美式", coffee),
            ("手沖", coffee),
            ("卡布", coffee),
            ("作廢的", coffee),
            ("退貨的", coffee),
            ("戚風", dessert),
        ]
    ):
        item = MenuItem(
            store_id=store.id,
            name=name,
            unit_price=Decimal("100"),
            category_id=category.id,
            sort_order=order,
        )
        session.add(item)
        await session.flush()
        ctx.items[name] = item.id
    return ctx


async def _sell(session: AsyncSession, ctx: Ctx, name: str, qty: int) -> Sale:
    return await SalesService(session).create_sale(
        ctx.store_id,
        ctx.manager_id,
        lines=[SaleLineInput(line_type=SaleLineType.MENU, menu_item_id=ctx.items[name], qty=qty)],
        tenders=[TenderInput(tender_type=TenderType.CASH, amount=Decimal(100 * qty))],
        service_mode=ServiceMode.TAKEOUT,
    )


async def test_defaults_before_anything_is_saved(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _seed(db_session)
    resp = await client.get(URL, headers={"Authorization": f"Bearer {ctx.clerk}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["is_active"], body["window_days"], body["min_qty"]) == (True, 30, 10)
    assert body["ranking"] == []


async def test_only_managers_change_settings_and_it_is_audited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _seed(db_session)
    body = {"is_active": False, "window_days": 7, "min_qty": 5}
    clerk = await client.put(URL, json=body, headers={"Authorization": f"Bearer {ctx.clerk}"})
    assert clerk.status_code == 403
    for bad in ({**body, "window_days": 10}, {**body, "min_qty": 0}, {**body, "min_qty": 1000}):
        resp = await client.put(URL, json=bad, headers={"Authorization": f"Bearer {ctx.manager}"})
        assert resp.status_code == 422, bad
    saved = await client.put(URL, json=body, headers={"Authorization": f"Bearer {ctx.manager}"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["window_days"] == 7
    log = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "UPDATE_ONLINE_MENU_POPULARITY")
    )
    assert log is not None and log.after == body


async def test_top_three_per_category_above_threshold_net_of_voids_and_returns(
    db_session: AsyncSession,
) -> None:
    ctx = await _seed(db_session)
    for name, qty in [("拿鐵", 15), ("美式", 12), ("手沖", 11), ("卡布", 10), ("戚風", 9)]:
        await _sell(db_session, ctx, name, qty)
    voided = await _sell(db_session, ctx, "作廢的", 30)
    await SalesService(db_session).void_sale(voided, ctx.manager_id)
    returned = await _sell(db_session, ctx, "退貨的", 20)
    line_id = await db_session.scalar(select(SaleLine.id).where(SaleLine.sale_id == returned.id))
    assert line_id is not None
    await ReturnsService(db_session).create_return(
        ctx.store_id,
        sale_id=returned.id,
        lines=[ReturnLineInput(sale_line_id=line_id, qty=11)],
        reason="客人退",
        actor_user_id=ctx.manager_id,
        idempotency_key=f"pop-return-{ctx.store_id}",
    )

    ranks = await PopularityService(db_session).ranks(ctx.store_id)

    # 退貨的：20 − 11 ＝ 9 不到門檻；作廢的不算；卡布第四名；戚風 9 份不到門檻
    assert ranks == {ctx.items["拿鐵"]: 1, ctx.items["美式"]: 2, ctx.items["手沖"]: 3}


async def test_sales_before_the_window_do_not_count(db_session: AsyncSession) -> None:
    ctx = await _seed(db_session)
    old = await _sell(db_session, ctx, "拿鐵", 50)
    await db_session.execute(
        update(Sale).where(Sale.id == old.id).values(created_at=utc_now() - timedelta(days=31))
    )
    await _sell(db_session, ctx, "美式", 10)

    assert await PopularityService(db_session).ranks(ctx.store_id) == {ctx.items["美式"]: 1}


async def test_turned_off_means_no_labels_and_preview_lists_names(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _seed(db_session)
    await _sell(db_session, ctx, "拿鐵", 12)
    preview = (await client.get(URL, headers={"Authorization": f"Bearer {ctx.clerk}"})).json()
    assert preview["ranking"] == [
        {"category": "咖啡", "item_id": ctx.items["拿鐵"], "name": "拿鐵", "rank": 1, "qty": 12}
    ]
    await client.put(
        URL,
        json={"is_active": False, "window_days": 30, "min_qty": 10},
        headers={"Authorization": f"Bearer {ctx.manager}"},
    )
    assert await PopularityService(db_session).ranks(ctx.store_id) == {}


async def test_labels_ride_along_with_the_availability_sync(db_session: AsyncSession) -> None:
    """人氣跟著可售狀態同步推上雲端：賣出去就會更新，不必重新發佈。"""
    ctx = await _seed(db_session)
    await _publish(db_session, ctx.store_id, ctx.manager_id, 100)
    service = AvailabilityService(db_session)
    first = await service.capture(ctx.store_id)
    assert first is not None and first["popular"] == []

    await _sell(db_session, ctx, "拿鐵", 10)
    second = await service.capture(ctx.store_id)
    assert second is not None and second["revision"] == first["revision"] + 1
    assert second["popular"] == [{"id": ctx.items["拿鐵"], "rank": 1}]


async def test_items_taken_off_the_menu_do_not_take_a_rank(db_session: AsyncSession) -> None:
    """下架的品項客人看不到，不能佔掉名次（Codex 第一輪）。"""
    ctx = await _seed(db_session)
    await _sell(db_session, ctx, "拿鐵", 30)
    await _sell(db_session, ctx, "美式", 12)
    latte = await db_session.get(MenuItem, ctx.items["拿鐵"])
    assert latte is not None
    latte.is_available = False
    await db_session.flush()

    assert await PopularityService(db_session).ranks(ctx.store_id) == {ctx.items["美式"]: 1}


async def test_uncategorized_items_rank_as_their_own_group(db_session: AsyncSession) -> None:
    """沒設分類的品項自成一組，一樣取前三（Codex 第一輪）。"""
    ctx = await _seed(db_session)
    loose = MenuItem(store_id=ctx.store_id, name="沒分類的", unit_price=Decimal("100"))
    db_session.add(loose)
    await db_session.flush()
    ctx.items["沒分類的"] = loose.id
    await _sell(db_session, ctx, "沒分類的", 10)

    service = PopularityService(db_session)
    assert await service.ranks(ctx.store_id) == {loose.id: 1}
    [row] = (await service.read(ctx.store_id)).ranking
    assert (row.category, row.name) == ("未分類", "沒分類的")
