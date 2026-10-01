"""menu repository：唯一直接碰菜單相關資料表的層（§2）。"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import ColumnElement, case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.menu.models import (
    MenuCategory,
    MenuItem,
    MenuItemOptionGroup,
    MenuOption,
    MenuOptionGroup,
    MenuStockAdjustment,
)
from app.shared.enums import MenuStockAdjustReason


class MenuRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def delete(self, item: MenuItem) -> None:
        await self._session.execute(
            delete(MenuItemOptionGroup).where(MenuItemOptionGroup.item_id == item.id)
        )
        await self._session.delete(item)
        await self._session.flush()

    async def add[T: (MenuItem, MenuCategory, MenuOptionGroup, MenuOption)](self, row: T) -> T:
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, store_id: int, item_id: int) -> MenuItem | None:
        """取單一品項（含已封存；供管理/結帳解析）。"""
        item: MenuItem | None = await self._session.scalar(
            select(MenuItem).where(MenuItem.store_id == store_id, MenuItem.id == item_id)
        )
        return item

    async def get_for_update(self, store_id: int, item_id: int) -> MenuItem | None:
        item: MenuItem | None = await self._session.scalar(
            select(MenuItem)
            .where(MenuItem.store_id == store_id, MenuItem.id == item_id)
            .with_for_update()
        )
        return item

    async def name_exists(self, store_id: int, name: str, *, exclude_id: int | None = None) -> bool:
        """同店是否已有同名（未封存）品項——建立/改名去重。"""
        stmt = select(MenuItem.id).where(
            MenuItem.store_id == store_id,
            MenuItem.name == name,
            MenuItem.archived_at.is_(None),
        )
        if exclude_id is not None:
            stmt = stmt.where(MenuItem.id != exclude_id)
        return (await self._session.scalar(stmt.limit(1))) is not None

    # ── 分類 ──

    async def list_categories(self, store_id: int) -> list[MenuCategory]:
        stmt = (
            select(MenuCategory)
            .where(MenuCategory.store_id == store_id, MenuCategory.archived_at.is_(None))
            .order_by(MenuCategory.sort_order, MenuCategory.name)
        )
        return list((await self._session.scalars(stmt)).all())

    async def get_categories(self, store_id: int, ids: Sequence[int]) -> list[MenuCategory]:
        if not ids:
            return []
        stmt = select(MenuCategory).where(
            MenuCategory.store_id == store_id, MenuCategory.id.in_(ids)
        )
        return list((await self._session.scalars(stmt)).all())

    async def get_category(
        self, store_id: int, category_id: int, *, for_update: bool = False
    ) -> MenuCategory | None:
        stmt = select(MenuCategory).where(
            MenuCategory.store_id == store_id,
            MenuCategory.id == category_id,
            MenuCategory.archived_at.is_(None),
        )
        if for_update:
            stmt = stmt.with_for_update()
        category: MenuCategory | None = await self._session.scalar(stmt)
        return category

    async def find_category_by_name(self, store_id: int, name: str) -> MenuCategory | None:
        category: MenuCategory | None = await self._session.scalar(
            select(MenuCategory).where(
                MenuCategory.store_id == store_id,
                MenuCategory.name == name,
                MenuCategory.archived_at.is_(None),
            )
        )
        return category

    async def clear_category(self, store_id: int, category_id: int) -> None:
        """分類封存時，原本掛在它底下的品項改為未分類。"""
        items = await self._session.scalars(
            select(MenuItem).where(
                MenuItem.store_id == store_id, MenuItem.category_id == category_id
            )
        )
        for item in items:
            item.category_id = None
        await self._session.flush()

    # ── 選項群組／選項 ──

    async def list_groups(self, store_id: int) -> list[MenuOptionGroup]:
        stmt = (
            select(MenuOptionGroup)
            .where(MenuOptionGroup.store_id == store_id, MenuOptionGroup.archived_at.is_(None))
            .order_by(MenuOptionGroup.sort_order, MenuOptionGroup.name)
        )
        return list((await self._session.scalars(stmt)).all())

    async def get_group(
        self, store_id: int, group_id: int, *, for_update: bool = False
    ) -> MenuOptionGroup | None:
        stmt = select(MenuOptionGroup).where(
            MenuOptionGroup.store_id == store_id,
            MenuOptionGroup.id == group_id,
            MenuOptionGroup.archived_at.is_(None),
        )
        if for_update:
            stmt = stmt.with_for_update()
        group: MenuOptionGroup | None = await self._session.scalar(stmt)
        return group

    async def get_groups(self, store_id: int, ids: Sequence[int]) -> list[MenuOptionGroup]:
        if not ids:
            return []
        stmt = select(MenuOptionGroup).where(
            MenuOptionGroup.store_id == store_id,
            MenuOptionGroup.id.in_(ids),
            MenuOptionGroup.archived_at.is_(None),
        )
        return list((await self._session.scalars(stmt)).all())

    async def group_name_exists(
        self, store_id: int, name: str, *, exclude_id: int | None = None
    ) -> bool:
        stmt = select(MenuOptionGroup.id).where(
            MenuOptionGroup.store_id == store_id,
            MenuOptionGroup.name == name,
            MenuOptionGroup.archived_at.is_(None),
        )
        if exclude_id is not None:
            stmt = stmt.where(MenuOptionGroup.id != exclude_id)
        return (await self._session.scalar(stmt.limit(1))) is not None

    async def list_options(self, group_ids: Sequence[int]) -> list[MenuOption]:
        """多個群組的未封存選項，依群組內 sort_order、建立順序排。"""
        if not group_ids:
            return []
        stmt = (
            select(MenuOption)
            .where(MenuOption.group_id.in_(group_ids), MenuOption.archived_at.is_(None))
            .order_by(MenuOption.group_id, MenuOption.sort_order, MenuOption.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def get_option(
        self, store_id: int, option_id: int, *, for_update: bool = False
    ) -> MenuOption | None:
        stmt = select(MenuOption).where(
            MenuOption.store_id == store_id,
            MenuOption.id == option_id,
            MenuOption.archived_at.is_(None),
        )
        if for_update:
            stmt = stmt.with_for_update()
        option: MenuOption | None = await self._session.scalar(stmt)
        return option

    async def option_name_exists(
        self, group_id: int, name: str, *, exclude_id: int | None = None
    ) -> bool:
        stmt = select(MenuOption.id).where(
            MenuOption.group_id == group_id,
            MenuOption.name == name,
            MenuOption.archived_at.is_(None),
        )
        if exclude_id is not None:
            stmt = stmt.where(MenuOption.id != exclude_id)
        return (await self._session.scalar(stmt.limit(1))) is not None

    async def next_option_sort(self, group_id: int) -> int:
        current = await self._session.scalar(
            select(MenuOption.sort_order)
            .where(MenuOption.group_id == group_id)
            .order_by(MenuOption.sort_order.desc())
            .limit(1)
        )
        return 0 if current is None else current + 1

    # ── 品項 ↔ 群組 ──

    async def replace_item_groups(
        self, store_id: int, item_id: int, group_ids: Sequence[int]
    ) -> None:
        await self._session.execute(
            delete(MenuItemOptionGroup).where(MenuItemOptionGroup.item_id == item_id)
        )
        self._session.add_all(
            MenuItemOptionGroup(item_id=item_id, group_id=gid, store_id=store_id, sort_order=i)
            for i, gid in enumerate(group_ids)
        )
        await self._session.flush()

    async def item_group_links(self, item_ids: Sequence[int]) -> list[MenuItemOptionGroup]:
        """品項所掛的群組（依掛載順序；已封存群組排除）。"""
        if not item_ids:
            return []
        stmt = (
            select(MenuItemOptionGroup)
            .join(MenuOptionGroup, MenuOptionGroup.id == MenuItemOptionGroup.group_id)
            .where(
                MenuItemOptionGroup.item_id.in_(item_ids),
                MenuOptionGroup.archived_at.is_(None),
            )
            .order_by(MenuItemOptionGroup.item_id, MenuItemOptionGroup.sort_order)
        )
        return list((await self._session.scalars(stmt)).all())

    # ── 每日限量（docs/44 §3.7）──
    # 全部是**單一句條件式 UPDATE**：Postgres 會鎖住該列、等前一筆提交後重新檢查 WHERE，
    # 所以兩台同時賣最後一份、或結帳與店員調整同時發生，都只會有一邊成功，不會扣成負數。

    @staticmethod
    def _today_qty(model: type[MenuItem] | type[MenuOption], today: date) -> ColumnElement[int]:
        """今天的有效份數：stock_day 不是今天＝已歸零。"""
        return case((model.stock_day == today, model.stock_qty), else_=0)

    async def consume_stock(
        self,
        model: type[MenuItem] | type[MenuOption],
        store_id: int,
        row_id: int,
        qty: int,
        today: date,
    ) -> int | None:
        """扣 qty 份，回傳扣到的份數版本；今天沒填或不夠 → None（不動任何資料）。"""
        result = await self._session.execute(
            update(model)
            .where(
                model.store_id == store_id,
                model.id == row_id,
                model.daily_limited.is_(True),
                model.stock_day == today,
                model.stock_qty >= qty,
            )
            .values(stock_qty=model.stock_qty - qty)
            .returning(model.stock_generation)
        )
        generation: int | None = result.scalar_one_or_none()
        return generation

    async def add_stock(
        self,
        model: type[MenuItem] | type[MenuOption],
        store_id: int,
        row_id: int,
        delta: int,
        today: date,
    ) -> int | None:
        """今天的份數加減 delta（今天還沒填就從 0 起算）；會小於 0 或不是限量 → None。"""
        current = self._today_qty(model, today)
        result = await self._session.execute(
            update(model)
            .where(
                model.store_id == store_id,
                model.id == row_id,
                model.daily_limited.is_(True),
                current + delta >= 0,
            )
            .values(stock_qty=current + delta, stock_day=today)
            .returning(model.stock_qty)
        )
        value: int | None = result.scalar_one_or_none()
        return value

    async def set_stock(
        self,
        model: type[MenuItem] | type[MenuOption],
        store_id: int,
        row_id: int,
        qty: int,
        expected: int,
        today: date,
    ) -> int | None:
        """把今天的份數改成 qty——**前提是現在還是店員看到的 expected**，否則 None。

        沒有這個前提，店員看到 3 想改 10 的同時有人賣掉 1 份，那份就被「賣回來」了。
        """
        result = await self._session.execute(
            update(model)
            .where(
                model.store_id == store_id,
                model.id == row_id,
                model.daily_limited.is_(True),
                self._today_qty(model, today) == expected,
            )
            .values(stock_qty=qty, stock_day=today, stock_generation=model.stock_generation + 1)
            .returning(model.stock_qty)
        )
        value: int | None = result.scalar_one_or_none()
        return value

    async def restore_stock(
        self,
        model: type[MenuItem] | type[MenuOption],
        store_id: int,
        row_id: int,
        qty: int,
        generation: int,
        day: date,
    ) -> None:
        """作廢加回：只加回同一個營業日、且份數版本沒變（賣出後沒人重設過）的份數。

        重設（「改成」或切換每日限量）代表店員實際數過，數字已反映現況，再加回會多算。
        """
        await self._session.execute(
            update(model)
            .where(
                model.store_id == store_id,
                model.id == row_id,
                model.stock_day == day,
                model.stock_generation == generation,
            )
            .values(stock_qty=model.stock_qty + qty)
        )

    async def waste_summary(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> list[tuple[MenuStockAdjustReason, int, Decimal, int]]:
        qty = -MenuStockAdjustment.delta
        rows = await self._session.execute(
            select(
                MenuStockAdjustment.reason,
                func.coalesce(func.sum(qty), 0),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                MenuStockAdjustment.unit_cost_snapshot.is_not(None),
                                MenuStockAdjustment.unit_cost_snapshot * qty,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case((MenuStockAdjustment.unit_cost_snapshot.is_(None), qty), else_=0)
                    ),
                    0,
                ),
            )
            .where(
                MenuStockAdjustment.store_id == store_id,
                MenuStockAdjustment.reason.in_(
                    [MenuStockAdjustReason.WASTE.value, MenuStockAdjustReason.CORRECTION.value]
                ),
                MenuStockAdjustment.created_at >= date_from,
                MenuStockAdjustment.created_at < date_to,
            )
            .group_by(MenuStockAdjustment.reason)
        )
        return [
            (MenuStockAdjustReason(reason), int(q), Decimal(cost), int(unknown))
            for reason, q, cost, unknown in rows
        ]

    async def add_adjustment(self, row: MenuStockAdjustment) -> None:
        self._session.add(row)
        await self._session.flush()

    async def list_limited_items(self, store_id: int) -> list[MenuItem]:
        stmt = (
            select(MenuItem)
            .where(
                MenuItem.store_id == store_id,
                MenuItem.daily_limited.is_(True),
                MenuItem.archived_at.is_(None),
                MenuItem.is_available.is_(True),
            )
            .order_by(MenuItem.sort_order, MenuItem.name)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_limited_options(self, store_id: int) -> list[tuple[MenuOption, MenuOptionGroup]]:
        stmt = (
            select(MenuOption, MenuOptionGroup)
            .join(MenuOptionGroup, MenuOptionGroup.id == MenuOption.group_id)
            .where(
                MenuOption.store_id == store_id,
                MenuOption.daily_limited.is_(True),
                MenuOption.archived_at.is_(None),
                MenuOption.is_available.is_(True),
                MenuOptionGroup.archived_at.is_(None),
            )
            .order_by(MenuOptionGroup.sort_order, MenuOptionGroup.name, MenuOption.sort_order)
        )
        return [(o, g) for o, g in (await self._session.execute(stmt)).all()]

    async def get_option_any(self, store_id: int, option_id: int) -> MenuOption | None:
        """含已封存（作廢加回用：選項封存了，當天賣出的份數仍要加回）。"""
        option: MenuOption | None = await self._session.scalar(
            select(MenuOption).where(MenuOption.store_id == store_id, MenuOption.id == option_id)
        )
        return option

    # 放在最後：方法名 `list` 會在類別內遮蔽內建 list，排在它之後的型別註記就壞了。
    async def list(self, store_id: int, *, include_unavailable: bool) -> list[MenuItem]:
        """列出未封存品項（管理頁全列、POS 只列可售）；依 sort_order、name 排序。"""
        stmt = select(MenuItem).where(MenuItem.store_id == store_id, MenuItem.archived_at.is_(None))
        if not include_unavailable:
            stmt = stmt.where(MenuItem.is_available.is_(True))
        stmt = stmt.order_by(MenuItem.sort_order, MenuItem.name)
        return list((await self._session.scalars(stmt)).all())
