"""menu 業務邏輯：餐飲菜單品項 CRUD（建立／改名改價／上下架／封存）。

本層只 flush、不 commit（由呼叫端控制）。改價屬敏感操作 → 寫 audit_log（§5）。
金額為含稅整數元（§6）：unit_price 必須為正整數元。
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.menu.models import MenuItem
from app.modules.menu.repository import MenuRepository
from app.shared.exceptions import (
    DuplicateMenuItem,
    MenuItemNotFound,
    SaleLineInvalid,
)

# 區分「未提供（不變）」與「明確設為 None（清空）」——目前僅 category 需要清空語意。
_UNSET: Final = object()


def _validate_price(unit_price: Decimal) -> None:
    if unit_price != unit_price.to_integral_value():
        raise SaleLineInvalid("菜單售價必須為整數元")
    if unit_price <= 0:
        raise SaleLineInvalid("菜單售價必須為正")


class MenuService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = MenuRepository(session)

    async def create_menu_item(
        self,
        store_id: int,
        *,
        name: str,
        unit_price: Decimal,
        category: str | None = None,
        sort_order: int = 0,
        actor_user_id: int,
    ) -> MenuItem:
        _validate_price(unit_price)
        if await self._repo.name_exists(store_id, name):
            raise DuplicateMenuItem(f"已有同名菜單品項：{name}")
        item = await self._repo.add(
            MenuItem(
                store_id=store_id,
                name=name,
                unit_price=unit_price,
                category=category,
                sort_order=sort_order,
            )
        )
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="CREATE_MENU_ITEM",
            entity_type="menu_item",
            entity_id=str(item.id),
            after={"name": name, "unit_price": str(unit_price)},
        )
        return item

    async def update_menu_item(
        self,
        store_id: int,
        item_id: int,
        *,
        name: str | None = None,
        unit_price: Decimal | None = None,
        category: str | None | object = _UNSET,
        sort_order: int | None = None,
        is_available: bool | None = None,
        actor_user_id: int,
    ) -> MenuItem:
        """部分更新（None=不變；category 另以 _UNSET 區分「不變」與「清空」）。改價寫稽核。"""
        item = await self._repo.get_for_update(store_id, item_id)
        if item is None or item.archived_at is not None:
            raise MenuItemNotFound(f"找不到菜單品項 {item_id}")

        before_price = item.unit_price
        if name is not None and name != item.name:
            if await self._repo.name_exists(store_id, name, exclude_id=item_id):
                raise DuplicateMenuItem(f"已有同名菜單品項：{name}")
            item.name = name
        if unit_price is not None:
            _validate_price(unit_price)
            item.unit_price = unit_price
        if category is not _UNSET:
            item.category = category  # type: ignore[assignment]
        if sort_order is not None:
            item.sort_order = sort_order
        if is_available is not None:
            item.is_available = is_available
        await self._session.flush()

        if unit_price is not None and unit_price != before_price:
            await write_audit_log(
                self._session,
                store_id=store_id,
                actor_user_id=actor_user_id,
                action="UPDATE_MENU_ITEM_PRICE",
                entity_type="menu_item",
                entity_id=str(item.id),
                before={"unit_price": str(before_price)},
                after={"unit_price": str(unit_price)},
            )
        return item

    async def archive_menu_item(
        self, store_id: int, item_id: int, *, actor_user_id: int
    ) -> MenuItem:
        """封存（軟刪除）：從 POS/管理清單隱藏，歷史 sale_line 參照仍有效。"""
        item = await self._repo.get_for_update(store_id, item_id)
        if item is None or item.archived_at is not None:
            raise MenuItemNotFound(f"找不到菜單品項 {item_id}")
        item.archived_at = datetime.now(UTC)
        item.is_available = False
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="ARCHIVE_MENU_ITEM",
            entity_type="menu_item",
            entity_id=str(item.id),
            before={"name": item.name},
        )
        return item

    # ── 查詢 ──
    async def get(self, store_id: int, item_id: int) -> MenuItem | None:
        return await self._repo.get(store_id, item_id)

    async def list_items(self, store_id: int, *, include_unavailable: bool) -> list[MenuItem]:
        return await self._repo.list(store_id, include_unavailable=include_unavailable)
