"""menu 業務邏輯：餐飲菜單品項 CRUD（建立／改名改價／上下架／封存）。

本層只 flush、不 commit（由呼叫端控制）。改價屬敏感操作 → 寫 audit_log（§5）。
金額為含稅整數元（§6）：unit_price 必須為正整數元。
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import MAX_NTD
from app.modules.menu.models import MenuItem
from app.modules.menu.repository import MenuRepository
from app.shared.exceptions import (
    DuplicateMenuItem,
    ItemDeleteBlocked,
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


def _validate_cost(unit_cost: Decimal | None) -> None:
    """成本：None＝未知；其餘須為 0 以上的整數元且不超過金額上限。

    不變量放在 service 而不只在 Pydantic：負成本會讓毛利報表憑空變大，而腳本／跨模組
    呼叫不經過 HTTP schema。0 是合法的「已知零成本」，與 None（未知）語意不同。
    """
    if unit_cost is None:
        return
    if unit_cost != unit_cost.to_integral_value():
        raise SaleLineInvalid("菜單成本必須為整數元")
    if unit_cost < 0:
        raise SaleLineInvalid("菜單成本不可為負")
    if unit_cost > MAX_NTD:
        raise SaleLineInvalid(f"菜單成本不可超過 {MAX_NTD}")


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
        unit_cost: Decimal | None = None,
        category: str | None = None,
        sort_order: int = 0,
        actor_user_id: int,
    ) -> MenuItem:
        _validate_price(unit_price)
        _validate_cost(unit_cost)
        if await self._repo.name_exists(store_id, name):
            raise DuplicateMenuItem(f"已有同名菜單品項：{name}")
        item = await self._repo.add(
            MenuItem(
                store_id=store_id,
                name=name,
                unit_price=unit_price,
                unit_cost=unit_cost,
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
            after={
                "name": name,
                "unit_price": str(unit_price),
                "unit_cost": None if unit_cost is None else str(unit_cost),
            },
        )
        return item

    async def update_menu_item(
        self,
        store_id: int,
        item_id: int,
        *,
        name: str | None = None,
        unit_price: Decimal | None = None,
        # 成本沿用 category 的 _UNSET 慣例：要能區分「沒提供（不變）」與「明確清空」。
        unit_cost: Decimal | None | object = _UNSET,
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
        before_cost = item.unit_cost
        if name is not None and name != item.name:
            if await self._repo.name_exists(store_id, name, exclude_id=item_id):
                raise DuplicateMenuItem(f"已有同名菜單品項：{name}")
            item.name = name
        if unit_price is not None:
            _validate_price(unit_price)
            item.unit_price = unit_price
        if unit_cost is not _UNSET:
            _validate_cost(unit_cost)  # type: ignore[arg-type]
            item.unit_cost = unit_cost  # type: ignore[assignment]
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
        # 成本直接決定毛利報表，改動同樣留前後值（含清空＝改回「未知」）。
        if unit_cost is not _UNSET and unit_cost != before_cost:
            await write_audit_log(
                self._session,
                store_id=store_id,
                actor_user_id=actor_user_id,
                action="UPDATE_MENU_ITEM_COST",
                entity_type="menu_item",
                entity_id=str(item.id),
                before={"unit_cost": None if before_cost is None else str(before_cost)},
                after={"unit_cost": None if unit_cost is None else str(unit_cost)},
            )
        return item

    async def delete_menu_item(self, store_id: int, item_id: int, *, actor_user_id: int) -> bool:
        """真刪誤建的菜單品項；賣過的擋下（裁示 2026-09-17）。找不到→False。

        與 archive（下架）不同：下架只是從清單/POS 隱藏，資料列還在。誤建的品項要真的
        消失，否則清單會愈積愈多垃圾。
        """
        from app.modules.customerdisplay.service import CustomerDisplayService
        from app.modules.sales.service import SalesService

        item = await self._repo.get_for_update(store_id, item_id)
        if item is None:
            return False
        if await SalesService(self._session).item_referenced_by_sales(
            store_id, menu_item_id=item_id
        ):
            raise ItemDeleteBlocked("這個品項賣過了，不能刪除，只能下架（交易紀錄要留著）")
        if await CustomerDisplayService(self._session).item_referenced_by_pending_payment(
            store_id, menu_item_id=item_id
        ):
            raise ItemDeleteBlocked("這個品項在一筆待確認付款的交易裡，補單完成前不能刪除")
        before = {"name": item.name, "unit_price": str(item.unit_price)}
        # 檢查通過到刪除之間若剛好被結帳用掉，外鍵會擋——那是可預期的衝突，回 409 不是 500。
        try:
            async with self._session.begin_nested():
                await self._repo.delete(item)
        except IntegrityError as exc:
            raise ItemDeleteBlocked("這個品項剛剛被結帳用到了，不能刪除") from exc
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="DELETE_MENU_ITEM",
            entity_type="menu_item",
            entity_id=str(item_id),
            before=before,
        )
        return True

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
