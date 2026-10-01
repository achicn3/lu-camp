"""餐飲每日限量（docs/44 §3.7，2026-10-01 裁示）。

- 勾「每日限量」的品項／選項：每天開店自動歸零（＝售完），店員在開店檢查頁填當天份數才能賣；
  沒勾的不限量（舊行為）。
- 營業中可動態調整：直接改成某數（附上看到的數字，被結帳搶先就拒絕）、或加減（原子）。
- 結帳原子扣減，同時搶最後一份只成交一筆；當天作廢加回，隔天作廢不加回（昨天的甜點不會回來）。
- 開店前檢查：還有沒填的限量品項就不算完成，可略過。
"""

from collections.abc import AsyncGenerator
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.core.time import store_date, utc_now
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.menu.models import MenuItem, MenuOption, MenuStockAdjustment
from app.modules.menu.service import MenuService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole
from tests.integration.customer_display_helpers import CustomerDisplayAwareClient


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with CustomerDisplayAwareClient(
        transport=transport, base_url="http://test", db_session=db_session
    ) as c:
        yield c
    app.dependency_overrides.clear()


class _Ctx:
    def __init__(self) -> None:
        self.store_id = 0
        self.clerk = ""
        self.mgr = ""
        self.mgr_id = 0
        self.cake = 0  # 每日限量甜點
        self.latte = 0  # 不限量，但「豆種」選項中的「藝伎」每日限量
        self.geisha = 0
        self.house = 0


async def _seed(session: AsyncSession) -> _Ctx:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    session.add_all([clerk, mgr])
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    svc = MenuService(session)
    cake = await svc.create_menu_item(
        store.id, name="戚風", unit_price=Decimal(90), actor_user_id=mgr.id
    )
    await svc.update_menu_item(store.id, cake.id, daily_limited=True, actor_user_id=mgr.id)
    latte = await svc.create_menu_item(
        store.id, name="手沖", unit_price=Decimal(180), actor_user_id=mgr.id
    )
    beans = await svc.create_option_group(
        store.id,
        name="豆種",
        min_select=1,
        max_select=1,
        options=[("招牌", Decimal(0)), ("藝伎", Decimal(120))],
        actor_user_id=mgr.id,
    )
    await svc.set_item_option_groups(store.id, latte.id, [beans.group.id], actor_user_id=mgr.id)
    house, geisha = beans.options
    await svc.update_option(store.id, geisha.id, daily_limited=True, actor_user_id=mgr.id)
    c = _Ctx()
    c.store_id = store.id
    c.clerk = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    c.mgr = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
    c.mgr_id = mgr.id
    c.cake, c.latte, c.geisha, c.house = cake.id, latte.id, geisha.id, house.id
    return c


def _h(token: str, idem: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        h["Idempotency-Key"] = idem
    return h


async def _sell(
    client: httpx.AsyncClient, c: _Ctx, lines: list[dict[str, object]], idem: str
) -> httpx.Response:
    return await client.post(
        "/api/v1/sales",
        json={"lines": lines, "service_mode": "TAKEOUT"},
        headers=_h(c.clerk, idem),
    )


def _cake(c: _Ctx, qty: int = 1) -> dict[str, object]:
    return {"line_type": "MENU", "menu_item_id": c.cake, "qty": qty}


def _pourover(c: _Ctx, option: int, qty: int = 1) -> dict[str, object]:
    return {"line_type": "MENU", "menu_item_id": c.latte, "qty": qty, "menu_option_ids": [option]}


async def _set(
    client: httpx.AsyncClient, c: _Ctx, kind: str, target: int, qty: int, expected: int
) -> httpx.Response:
    return await client.post(
        f"/api/v1/menu-daily-stock/{kind}/{target}/set",
        json={"qty": qty, "expected_remaining": expected},
        headers=_h(c.clerk),
    )


async def _adjust(
    client: httpx.AsyncClient,
    c: _Ctx,
    kind: str,
    target: int,
    delta: int,
    reason: str | None = None,
) -> httpx.Response:
    if reason is None:
        reason = "RESTOCK" if delta > 0 else "WASTE"
    return await client.post(
        f"/api/v1/menu-daily-stock/{kind}/{target}/adjust",
        json={"delta": delta, "reason": reason},
        headers=_h(c.clerk),
    )


async def _remaining(client: httpx.AsyncClient, c: _Ctx) -> dict[tuple[str, int], int]:
    resp = await client.get("/api/v1/menu-daily-stock", headers=_h(c.clerk))
    assert resp.status_code == 200, resp.text
    return {(e["kind"], e["id"]): e["remaining"] for e in resp.json()}


# ── 歸零與填數量 ──


async def test_limited_item_is_sold_out_until_set_today(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    resp = await _sell(client, c, [_cake(c)], "s1")
    assert resp.status_code == 409, resp.text
    assert "還沒設定數量" in resp.json()["detail"]


async def test_set_quantity_then_sell_down_to_zero(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """結帳失敗會回滾整個測試交易，所以「賣不出去」放在最後一步驗。"""
    c = await _seed(db_session)
    assert (await _set(client, c, "item", c.cake, 3, expected=0)).status_code == 200
    assert (await _sell(client, c, [_cake(c, 2)], "s2")).status_code == 201
    assert (await _remaining(client, c))[("item", c.cake)] == 1
    assert (await _sell(client, c, [_cake(c)], "s4")).status_code == 201
    assert (await _remaining(client, c))[("item", c.cake)] == 0
    sold_out = await _sell(client, c, [_cake(c)], "s5")
    assert sold_out.status_code == 409
    assert "已售完" in sold_out.json()["detail"]


async def test_selling_more_than_left_says_how_many_left(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    await _set(client, c, "item", c.cake, 1, expected=0)
    over = await _sell(client, c, [_cake(c, 2)], "s3")
    assert over.status_code == 409
    assert "只剩 1" in over.json()["detail"]


async def test_yesterdays_quantity_does_not_carry_over(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """昨天填的 5 份今天不算：每日歸零是「以營業日判斷」，不靠排程，機器關機也不會漏。"""
    c = await _seed(db_session)
    cake = await db_session.get(MenuItem, c.cake)
    assert cake is not None
    cake.stock_qty = 5
    cake.stock_day = store_date(utc_now()) - timedelta(days=1)
    await db_session.flush()
    assert (await _remaining(client, c))[("item", c.cake)] == 0
    assert (await _sell(client, c, [_cake(c)], "y1")).status_code == 409


async def test_unlimited_items_and_options_sell_freely(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    resp = await _sell(client, c, [_pourover(c, c.house, qty=50)], "u1")
    assert resp.status_code == 201, resp.text
    entries = await _remaining(client, c)
    assert ("item", c.latte) not in entries  # 只列每日限量的
    assert ("option", c.house) not in entries


async def test_limited_option_is_consumed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    assert (await _set(client, c, "option", c.geisha, 2, expected=0)).status_code == 200
    assert (await _sell(client, c, [_pourover(c, c.geisha, qty=2)], "o2")).status_code == 201
    assert (await _remaining(client, c))[("option", c.geisha)] == 0
    sold_out = await _sell(client, c, [_pourover(c, c.geisha)], "o3")
    assert sold_out.status_code == 409
    assert "藝伎" in sold_out.json()["detail"]


async def test_limited_option_not_set_today_blocks_sale(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    resp = await _sell(client, c, [_pourover(c, c.geisha)], "o1")
    assert resp.status_code == 409
    assert "藝伎" in resp.json()["detail"]


async def test_turning_off_daily_limit_makes_item_unlimited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    resp = await client.patch(
        f"/api/v1/menu-items/{c.cake}", json={"daily_limited": False}, headers=_h(c.mgr)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["daily_limited"] is False
    assert resp.json()["remaining"] is None
    assert (await _sell(client, c, [_cake(c, 10)], "off1")).status_code == 201


async def test_clerk_cannot_toggle_daily_limit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    resp = await client.patch(
        f"/api/v1/menu-items/{c.cake}", json={"daily_limited": False}, headers=_h(c.clerk)
    )
    assert resp.status_code == 403


# ── 營業中調整與競爭 ──


async def test_adjust_adds_and_subtracts_atomically(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    await _set(client, c, "item", c.cake, 3, expected=0)
    resp = await _adjust(client, c, "item", c.cake, 4)
    assert resp.status_code == 200, resp.text
    assert resp.json()["remaining"] == 7
    assert (await _adjust(client, c, "item", c.cake, -2)).json()["remaining"] == 5
    below = await _adjust(client, c, "item", c.cake, -6)
    assert below.status_code == 409
    assert (await _remaining(client, c))[("item", c.cake)] == 5


async def test_adjust_before_setting_today_starts_from_zero(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """今天還沒填就按「+4」：從 0 開始加（昨天剩的不算），不是從昨天的數字加。"""
    c = await _seed(db_session)
    cake = await db_session.get(MenuItem, c.cake)
    assert cake is not None
    cake.stock_qty = 9
    cake.stock_day = store_date(utc_now()) - timedelta(days=1)
    await db_session.flush()
    resp = await _adjust(client, c, "item", c.cake, 4)
    assert resp.status_code == 200, resp.text
    assert resp.json()["remaining"] == 4


async def test_set_with_stale_expected_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """店員看到 3 份想改成 10，期間有人賣掉 1 份：直接覆寫會把那份「賣回來」，必須拒絕。"""
    c = await _seed(db_session)
    await _set(client, c, "item", c.cake, 3, expected=0)
    await _sell(client, c, [_cake(c)], "race1")  # 剩 2
    stale = await _set(client, c, "item", c.cake, 10, expected=3)
    assert stale.status_code == 409
    assert "剛剛" in stale.json()["detail"]
    assert (await _remaining(client, c))[("item", c.cake)] == 2
    assert (await _set(client, c, "item", c.cake, 10, expected=2)).status_code == 200


async def test_negative_quantity_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    assert (await _set(client, c, "item", c.cake, -1, expected=0)).status_code == 422


async def test_stock_on_unlimited_target_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    resp = await _set(client, c, "item", c.latte, 5, expected=0)
    assert resp.status_code == 409
    assert "不限量" in resp.json()["detail"]


async def test_stock_changes_are_audited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    await _set(client, c, "item", c.cake, 3, expected=0)
    await _adjust(client, c, "item", c.cake, 2)
    actions = (
        await db_session.scalars(
            select(AuditLog.action).where(AuditLog.action.like("%MENU_DAILY_STOCK%"))
        )
    ).all()
    assert sorted(actions) == ["ADJUST_MENU_DAILY_STOCK", "SET_MENU_DAILY_STOCK"]


# ── 作廢 ──


async def test_void_today_puts_quantity_back(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    await _set(client, c, "item", c.cake, 2, expected=0)
    await _set(client, c, "option", c.geisha, 1, expected=0)
    sale = await _sell(client, c, [_cake(c, 2), _pourover(c, c.geisha)], "v1")
    assert sale.status_code == 201, sale.text
    voided = await client.post(
        f"/api/v1/sales/{sale.json()['id']}/void", json={"reason": "點錯"}, headers=_h(c.mgr)
    )
    assert voided.status_code == 200, voided.text
    remaining = await _remaining(client, c)
    assert remaining[("item", c.cake)] == 2
    assert remaining[("option", c.geisha)] == 1


async def test_void_after_the_day_changed_does_not_put_back(
    db_session: AsyncSession,
) -> None:
    """隔天才作廢：數量屬於今天的新一批，昨天賣掉的不能加進今天。"""
    c = await _seed(db_session)
    svc = MenuService(db_session)
    yesterday = store_date(utc_now()) - timedelta(days=1)
    await svc.set_daily_stock(
        c.store_id, "item", c.cake, qty=4, expected_remaining=0, actor_user_id=c.mgr_id
    )
    cake = await db_session.get(MenuItem, c.cake)
    assert cake is not None
    consumed: list[dict[str, object]] = [
        {
            "kind": "item",
            "id": c.cake,
            "generation": cake.stock_generation,
            "day": yesterday.isoformat(),
        }
    ]
    await svc.restore_daily_stock(c.store_id, consumed, qty=2)
    assert await svc.remaining(c.store_id, "item", c.cake) == 4


# ── 開店前檢查 ──


async def test_opening_check_waits_for_daily_quantities(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    await client.post(
        "/api/v1/opening-check/today/skip", json={"key": "cash_session"}, headers=_h(c.clerk)
    )
    today = (await client.get("/api/v1/opening-check/today", headers=_h(c.clerk))).json()
    assert today["menu_stock_pending"] == 2  # 戚風、藝伎都還沒填
    assert today["completed"] is False

    await _set(client, c, "item", c.cake, 6, expected=0)
    await _set(client, c, "option", c.geisha, 0, expected=0)  # 今天沒有藝伎：填 0 也算填過
    today = (await client.get("/api/v1/opening-check/today", headers=_h(c.clerk))).json()
    assert today["menu_stock_pending"] == 0
    assert today["completed"] is True


async def test_opening_check_menu_stock_can_be_skipped(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    for key in ("cash_session", "menu_stock"):
        resp = await client.post(
            "/api/v1/opening-check/today/skip", json={"key": key}, headers=_h(c.clerk)
        )
        assert resp.status_code == 200, resp.text
    today = (await client.get("/api/v1/opening-check/today", headers=_h(c.clerk))).json()
    assert today["completed"] is True


async def test_daily_stock_list_shows_set_today_flag_and_labels(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    await _set(client, c, "item", c.cake, 6, expected=0)
    rows = (await client.get("/api/v1/menu-daily-stock", headers=_h(c.clerk))).json()
    by_key = {(r["kind"], r["id"]): r for r in rows}
    assert by_key[("item", c.cake)]["label"] == "戚風"
    assert by_key[("item", c.cake)]["set_today"] is True
    assert by_key[("option", c.geisha)]["label"] == "豆種：藝伎"
    assert by_key[("option", c.geisha)]["set_today"] is False


async def test_option_archived_is_not_listed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    option = await db_session.get(MenuOption, c.geisha)
    assert option is not None
    await MenuService(db_session).archive_option(c.store_id, c.geisha)
    rows = await _remaining(client, c)
    assert ("option", c.geisha) not in rows


# ── 作廢加回的前提（Codex 對抗審查 O1c）＋ 調整原因 ──


async def _void(client: httpx.AsyncClient, c: _Ctx, sale_id: int) -> None:
    resp = await client.post(f"/api/v1/sales/{sale_id}/void", headers=_h(c.mgr))
    assert resp.status_code == 200, resp.text


async def test_void_after_a_recount_does_not_put_back(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """賣出後店員按了「改成」＝實際數過、數字已反映現況；之後作廢不能再加回（會多算）。"""
    c = await _seed(db_session)
    await _set(client, c, "item", c.cake, 5, expected=0)
    sale = await _sell(client, c, [_cake(c, 2)], "rc1")
    assert sale.status_code == 201, sale.text
    await _set(client, c, "item", c.cake, 10, expected=3)  # 店員數過：現在有 10 份
    await _void(client, c, sale.json()["id"])
    assert (await _remaining(client, c))[("item", c.cake)] == 10


async def test_void_after_only_adjustments_still_puts_back(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """賣出後只有 +1／−1（不是重數），作廢照樣加回。"""
    c = await _seed(db_session)
    await _set(client, c, "item", c.cake, 5, expected=0)
    sale = await _sell(client, c, [_cake(c, 2)], "ad1")
    await _adjust(client, c, "item", c.cake, 4)  # 3 → 7
    await _adjust(client, c, "item", c.cake, -1, "WASTE")  # 7 → 6
    await _void(client, c, sale.json()["id"])
    assert (await _remaining(client, c))[("item", c.cake)] == 8


async def test_void_after_limit_toggled_does_not_put_back(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    await _set(client, c, "item", c.cake, 5, expected=0)
    sale = await _sell(client, c, [_cake(c, 2)], "tg1")
    for flag in (False, True):
        resp = await client.patch(
            f"/api/v1/menu-items/{c.cake}", json={"daily_limited": flag}, headers=_h(c.mgr)
        )
        assert resp.status_code == 200
    await _set(client, c, "item", c.cake, 10, expected=0)
    await _void(client, c, sale.json()["id"])
    assert (await _remaining(client, c))[("item", c.cake)] == 10


async def test_decrease_requires_a_reason(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    await _set(client, c, "item", c.cake, 5, expected=0)
    resp = await client.post(
        f"/api/v1/menu-daily-stock/item/{c.cake}/adjust",
        json={"delta": -1},
        headers=_h(c.clerk),
    )
    assert resp.status_code == 422
    restock_as_decrease = await _adjust(client, c, "item", c.cake, -1, "RESTOCK")
    assert restock_as_decrease.status_code == 422


async def test_adjustments_are_recorded_with_reason_for_waste_stats(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    await _set(client, c, "item", c.cake, 5, expected=0)
    await _adjust(client, c, "item", c.cake, 3)
    await _adjust(client, c, "item", c.cake, -2, "WASTE")
    await _adjust(client, c, "item", c.cake, -1, "CORRECTION")
    rows = (
        await db_session.scalars(
            select(MenuStockAdjustment)
            .where(MenuStockAdjustment.store_id == c.store_id)
            .order_by(MenuStockAdjustment.id)
        )
    ).all()
    assert [(r.target_kind, r.target_id, r.delta, r.reason) for r in rows] == [
        ("item", c.cake, 3, "RESTOCK"),
        ("item", c.cake, -2, "WASTE"),
        ("item", c.cake, -1, "CORRECTION"),
    ]
    assert all(r.business_date == store_date(utc_now()) for r in rows)
