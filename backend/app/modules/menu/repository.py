"""menu repository：唯一直接碰 menu_items 資料表的層（§2）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.menu.models import MenuItem


class MenuRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, item: MenuItem) -> MenuItem:
        self._session.add(item)
        await self._session.flush()
        return item

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

    async def list(self, store_id: int, *, include_unavailable: bool) -> list[MenuItem]:
        """列出未封存品項（管理頁全列、POS 只列可售）；依 sort_order、name 排序。"""
        stmt = select(MenuItem).where(
            MenuItem.store_id == store_id, MenuItem.archived_at.is_(None)
        )
        if not include_unavailable:
            stmt = stmt.where(MenuItem.is_available.is_(True))
        stmt = stmt.order_by(MenuItem.sort_order, MenuItem.name)
        return list((await self._session.scalars(stmt)).all())
