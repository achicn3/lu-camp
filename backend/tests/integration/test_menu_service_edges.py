"""MenuService 邊界：找不到／重複／不合法設定都回領域例外，分類封存與品名截斷（docs/44 §3）。"""

from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.menu import service as menu_service
from app.modules.menu.models import MenuItem
from app.modules.menu.service import MenuService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole
from app.shared.exceptions import (
    DuplicateMenuEntry,
    DuplicateMenuItem,
    MenuEntryNotFound,
    MenuItemNotFound,
    SaleLineInvalid,
)

_MISSING = 999_999


async def _ctx(session: AsyncSession) -> tuple[MenuService, int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="m", password_hash="h", role=UserRole.MANAGER)
    session.add(mgr)
    await session.flush()
    return MenuService(session), store.id, mgr.id


@pytest.mark.parametrize("price", [Decimal("10.5"), Decimal(0), Decimal(-1)])
async def test_item_price_must_be_positive_whole(db_session: AsyncSession, price: Decimal) -> None:
    svc, store_id, uid = await _ctx(db_session)
    with pytest.raises(SaleLineInvalid):
        await svc.create_menu_item(store_id, name="x", unit_price=price, actor_user_id=uid)


@pytest.mark.parametrize("delta", [Decimal("1.5"), Decimal(-1), Decimal("1000000000000")])
async def test_option_price_delta_rules(db_session: AsyncSession, delta: Decimal) -> None:
    svc, store_id, uid = await _ctx(db_session)
    group = await svc.create_option_group(
        store_id, name="加購", min_select=0, max_select=1, actor_user_id=uid
    )
    with pytest.raises(SaleLineInvalid):
        await svc.add_option(
            store_id, group.group.id, name="x", price_delta=delta, actor_user_id=uid
        )


def test_long_description_is_truncated_with_ellipsis() -> None:
    picked = [("加購", f"選項{i:02d}" * 5) for i in range(20)]
    text = menu_service._line_description("拿鐵", picked)
    assert len(text) == menu_service.SALE_LINE_DESCRIPTION_MAX
    assert text.endswith("…")


async def test_update_item_rename_conflict_category_and_sort(db_session: AsyncSession) -> None:
    svc, store_id, uid = await _ctx(db_session)
    await svc.create_menu_item(store_id, name="美式", unit_price=Decimal(120), actor_user_id=uid)
    latte = await svc.create_menu_item(
        store_id, name="拿鐵", unit_price=Decimal(150), actor_user_id=uid
    )
    with pytest.raises(DuplicateMenuItem):
        await svc.update_menu_item(store_id, latte.id, name="美式", actor_user_id=uid)
    updated = await svc.update_menu_item(
        store_id, latte.id, category="咖啡", sort_order=5, actor_user_id=uid
    )
    assert updated.sort_order == 5
    [detail] = await svc.describe_items(store_id, [updated])
    assert detail.category is not None and detail.category.name == "咖啡"
    cleared = await svc.update_menu_item(store_id, latte.id, category="  ", actor_user_id=uid)
    assert cleared.category_id is None


async def test_category_errors_and_archive_uncategorizes_items(db_session: AsyncSession) -> None:
    svc, store_id, uid = await _ctx(db_session)
    coffee = await svc.create_category(store_id, name="咖啡")
    await svc.create_category(store_id, name="甜點")
    with pytest.raises(DuplicateMenuEntry):
        await svc.create_category(store_id, name="咖啡")
    with pytest.raises(DuplicateMenuEntry):
        await svc.update_category(store_id, coffee.id, name="甜點")
    with pytest.raises(MenuEntryNotFound):
        await svc.update_category(store_id, _MISSING, name="茶")
    with pytest.raises(MenuEntryNotFound):
        await svc.archive_category(store_id, _MISSING)

    item = await svc.create_menu_item(
        store_id, name="美式", unit_price=Decimal(120), category="咖啡", actor_user_id=uid
    )
    assert item.category_id == coffee.id
    await svc.archive_category(store_id, coffee.id)
    refreshed = await db_session.get(MenuItem, item.id)
    assert refreshed is not None and refreshed.category_id is None
    assert [c.name for c in await svc.list_categories(store_id)] == ["甜點"]


async def test_option_group_errors(db_session: AsyncSession) -> None:
    svc, store_id, uid = await _ctx(db_session)
    with pytest.raises(DuplicateMenuEntry):
        await svc.create_option_group(
            store_id,
            name="溫度",
            min_select=1,
            max_select=1,
            options=[("熱", Decimal(0)), ("熱", Decimal(0))],
            actor_user_id=uid,
        )
    temp = await svc.create_option_group(
        store_id, name="溫度", min_select=1, max_select=1, actor_user_id=uid
    )
    await svc.create_option_group(
        store_id, name="豆種", min_select=1, max_select=1, actor_user_id=uid
    )
    gid = temp.group.id
    with pytest.raises(DuplicateMenuEntry):
        await svc.update_option_group(store_id, gid, name="豆種")
    with pytest.raises(SaleLineInvalid):
        await svc.update_option_group(store_id, gid, min_select=2)  # 2 > max 1
    with pytest.raises(MenuEntryNotFound):
        await svc.update_option_group(store_id, _MISSING, name="x")
    with pytest.raises(MenuEntryNotFound):
        await svc.archive_option_group(store_id, _MISSING)
    with pytest.raises(MenuEntryNotFound):
        await svc.get_option_group(store_id, _MISSING)

    renamed = await svc.update_option_group(
        store_id, gid, name="冷熱", min_select=0, max_select=2, sort_order=4
    )
    assert (renamed.group.name, renamed.group.min_select, renamed.group.max_select) == (
        "冷熱",
        0,
        2,
    )
    assert renamed.group.sort_order == 4


async def test_option_errors(db_session: AsyncSession) -> None:
    svc, store_id, uid = await _ctx(db_session)
    group = await svc.create_option_group(
        store_id,
        name="奶",
        min_select=1,
        max_select=1,
        options=[("鮮奶", Decimal(0)), ("燕麥奶", Decimal(20))],
        actor_user_id=uid,
    )
    milk, oat = group.options
    with pytest.raises(MenuEntryNotFound):
        await svc.add_option(
            store_id, _MISSING, name="豆奶", price_delta=Decimal(10), actor_user_id=uid
        )
    with pytest.raises(DuplicateMenuEntry):
        await svc.add_option(
            store_id, group.group.id, name="鮮奶", price_delta=Decimal(0), actor_user_id=uid
        )
    with pytest.raises(DuplicateMenuEntry):
        await svc.update_option(store_id, oat.id, name="鮮奶", actor_user_id=uid)
    with pytest.raises(MenuEntryNotFound):
        await svc.update_option(store_id, _MISSING, name="x", actor_user_id=uid)
    with pytest.raises(MenuEntryNotFound):
        await svc.archive_option(store_id, _MISSING)

    moved = await svc.update_option(
        store_id, milk.id, name="全脂鮮奶", sort_order=9, actor_user_id=uid
    )
    assert (moved.name, moved.sort_order) == ("全脂鮮奶", 9)


async def test_set_groups_on_missing_item(db_session: AsyncSession) -> None:
    svc, store_id, uid = await _ctx(db_session)
    with pytest.raises(MenuItemNotFound):
        await svc.set_item_option_groups(store_id, _MISSING, [], actor_user_id=uid)


async def test_multi_select_minimum_message(db_session: AsyncSession) -> None:
    """至少選 2 項的群組只選 1 項：訊息講出最少要選幾項。"""
    svc, store_id, uid = await _ctx(db_session)
    item = await svc.create_menu_item(
        store_id, name="甜點拼盤", unit_price=Decimal(200), actor_user_id=uid
    )
    group = await svc.create_option_group(
        store_id,
        name="口味",
        min_select=2,
        max_select=3,
        options=[("抹茶", Decimal(0)), ("巧克力", Decimal(0)), ("檸檬", Decimal(0))],
        actor_user_id=uid,
    )
    await svc.set_item_option_groups(store_id, item.id, [group.group.id], actor_user_id=uid)
    with pytest.raises(SaleLineInvalid, match="至少要選 2 項"):
        await svc.price_selection(store_id, item, [group.options[0].id])
