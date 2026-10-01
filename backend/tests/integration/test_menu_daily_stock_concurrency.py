"""每日限量的真併發（docs/44 §3.7）：多條獨立連線同時操作、各自提交。

- 兩邊同時搶最後一份：只成交一份，份數不會變負。
- 十次同時「+1」：一次都不會被吃掉（加減是單一句原子 UPDATE）。
- 店員「直接改成 5」與結帳同時：要嘛結帳先、改數字被拒（看到的數字已過期），
  要嘛改數字先、結帳從 5 扣成 4——絕不會出現「賣掉的那份被覆寫回來」。
"""

import asyncio
from collections.abc import AsyncGenerator
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

import app.core.db as app_db
from app.modules.menu.service import MenuService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole
from app.shared.exceptions import InsufficientStock, MenuStockConflict


class _Seeded:
    def __init__(self, store_id: int, user_id: int, item_id: int) -> None:
        self.store_id = store_id
        self.user_id = user_id
        self.item_id = item_id


@pytest_asyncio.fixture
async def seeded() -> AsyncGenerator[_Seeded]:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="每日限量併發店")
        s.add(store)
        await s.flush()
        user = User(store_id=store.id, username="dl-mgr", password_hash="h", role=UserRole.MANAGER)
        s.add(user)
        await s.flush()
        svc = MenuService(s)
        cake = await svc.create_menu_item(
            store.id, name="戚風", unit_price=Decimal(90), actor_user_id=user.id
        )
        await svc.update_menu_item(store.id, cake.id, daily_limited=True, actor_user_id=user.id)
        await s.commit()
        data = _Seeded(store.id, user.id, cake.id)
    try:
        yield data
    finally:
        async with sm() as s:
            for sql in (
                "DELETE FROM audit_log WHERE store_id = :sid",
                "DELETE FROM menu_stock_adjustments WHERE store_id = :sid",
                "DELETE FROM menu_items WHERE store_id = :sid",
                "DELETE FROM users WHERE store_id = :sid",
                "DELETE FROM stores WHERE id = :sid",
            ):
                await s.execute(text(sql), {"sid": data.store_id})
            await s.commit()


async def _set(d: _Seeded, qty: int, expected: int) -> None:
    async with app_db.get_sessionmaker()() as s:
        await MenuService(s).set_daily_stock(
            d.store_id,
            "item",
            d.item_id,
            qty=qty,
            expected_remaining=expected,
            actor_user_id=d.user_id,
        )
        await s.commit()


async def _sell_one(d: _Seeded) -> bool:
    async with app_db.get_sessionmaker()() as s:
        svc = MenuService(s)
        item = await svc.get(d.store_id, d.item_id)
        assert item is not None
        try:
            selection = await svc.price_selection(d.store_id, item, [], 1)
            await svc.consume_daily_stock(d.store_id, item, selection, 1)
        except InsufficientStock:
            await s.rollback()
            return False
        await s.commit()
        return True


async def _remaining(d: _Seeded) -> int | None:
    async with app_db.get_sessionmaker()() as s:
        return await MenuService(s).remaining(d.store_id, "item", d.item_id)


async def test_two_registers_race_for_the_last_slice(seeded: _Seeded) -> None:
    await _set(seeded, 1, expected=0)
    results = await asyncio.gather(_sell_one(seeded), _sell_one(seeded))
    assert sorted(results) == [False, True]
    assert await _remaining(seeded) == 0


async def test_concurrent_adds_are_not_lost(seeded: _Seeded) -> None:
    await _set(seeded, 0, expected=0)

    async def add_one() -> None:
        async with app_db.get_sessionmaker()() as s:
            await MenuService(s).adjust_daily_stock(
                seeded.store_id, "item", seeded.item_id, delta=1, actor_user_id=seeded.user_id
            )
            await s.commit()

    await asyncio.gather(*(add_one() for _ in range(10)))
    assert await _remaining(seeded) == 10


@pytest.mark.parametrize("attempt", range(3))
async def test_overwrite_never_resurrects_a_sold_slice(seeded: _Seeded, attempt: int) -> None:
    await _set(seeded, 1, expected=0)

    async def overwrite() -> bool:
        try:
            await _set(seeded, 5, expected=1)  # 店員看到 1，想改成 5
        except MenuStockConflict:
            return False
        return True

    set_ok, sold = await asyncio.gather(overwrite(), _sell_one(seeded))
    final = await _remaining(seeded)
    if set_ok and sold:
        assert final == 4  # 先改成 5，再賣掉 1
    elif sold:
        assert final == 0  # 先賣掉，改數字因為看到的 1 已過期被拒
    else:
        pytest.fail(f"不應發生：set_ok={set_ok} sold={sold} final={final}")


async def test_limit_turned_on_mid_checkout_is_not_bypassed(seeded: _Seeded) -> None:
    """結帳讀到「不限量」後、扣份數前，管理者把它切成限量（Codex 對抗審查 O1c 第二輪）。

    扣份數前必須鎖住該列重讀，不能憑先前讀到的舊旗標跳過扣減——否則這筆會在限量
    生效後照樣成交、一份都沒扣。今天還沒填份數，所以正確結果是擋下。
    """
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        await MenuService(s).update_menu_item(
            seeded.store_id, seeded.item_id, daily_limited=False, actor_user_id=seeded.user_id
        )
        await s.commit()

    async with sm() as checkout:
        svc = MenuService(checkout)
        item = await svc.get(seeded.store_id, seeded.item_id)
        assert item is not None and item.daily_limited is False  # 結帳先讀到不限量
        selection = await svc.price_selection(seeded.store_id, item, [], 1)

        async with sm() as manager:  # 這時管理者切成每日限量並提交
            await MenuService(manager).update_menu_item(
                seeded.store_id, seeded.item_id, daily_limited=True, actor_user_id=seeded.user_id
            )
            await manager.commit()

        with pytest.raises(InsufficientStock):
            await svc.consume_daily_stock(seeded.store_id, item, selection, 1)
        await checkout.rollback()
