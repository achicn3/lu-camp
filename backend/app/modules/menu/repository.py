"""menu repository：唯一直接碰菜單相關資料表的層（§2）。"""

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.menu.models import (
    MenuCategory,
    MenuItem,
    MenuItemOptionGroup,
    MenuOption,
    MenuOptionGroup,
)


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

    # 放在最後：方法名 `list` 會在類別內遮蔽內建 list，排在它之後的型別註記就壞了。
    async def list(self, store_id: int, *, include_unavailable: bool) -> list[MenuItem]:
        """列出未封存品項（管理頁全列、POS 只列可售）；依 sort_order、name 排序。"""
        stmt = select(MenuItem).where(MenuItem.store_id == store_id, MenuItem.archived_at.is_(None))
        if not include_unavailable:
            stmt = stmt.where(MenuItem.is_available.is_(True))
        stmt = stmt.order_by(MenuItem.sort_order, MenuItem.name)
        return list((await self._session.scalars(stmt)).all())
