"""menu 業務邏輯：餐飲菜單品項、分類、選項群組與選項（docs/44 §3）。

本層只 flush、不 commit（由呼叫端控制）。改價（含選項加價）屬敏感操作 → 寫 audit_log（§5）。
金額為含稅整數元（§6）：unit_price 必須為正整數元；選項 price_delta 為 0 以上整數元。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import MAX_NTD, format_ntd
from app.modules.menu.models import MenuCategory, MenuItem, MenuOption, MenuOptionGroup
from app.modules.menu.repository import MenuRepository
from app.shared.exceptions import (
    DuplicateMenuEntry,
    DuplicateMenuItem,
    ItemDeleteBlocked,
    MenuEntryNotFound,
    MenuItemNotFound,
    MenuItemUnavailable,
    SaleLineInvalid,
)

# 銷售明細品名欄寬（sale_lines.description）；品名＋選項超過就截斷並以「…」結尾。
SALE_LINE_DESCRIPTION_MAX: Final = 300

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


def _validate_price_delta(price_delta: Decimal) -> None:
    if price_delta != price_delta.to_integral_value():
        raise SaleLineInvalid("選項加價必須為整數元")
    if price_delta < 0:
        raise SaleLineInvalid("選項加價不可為負")
    if price_delta > MAX_NTD:
        raise SaleLineInvalid(f"選項加價不可超過 {MAX_NTD}")


def _validate_bounds(min_select: int, max_select: int) -> None:
    """必選單選＝1/1、可選多選＝0/N；max 至少 1、min 不可大於 max。"""
    if min_select < 0 or max_select < 1 or min_select > max_select:
        raise SaleLineInvalid("可選數量設定不正確：最少不可小於 0、最多至少 1，且最少不可大於最多")


@dataclass(frozen=True)
class OptionGroupDetail:
    group: MenuOptionGroup
    options: list[MenuOption]


@dataclass(frozen=True)
class MenuItemDetail:
    """品項連同分類名稱與所掛群組（含未封存選項）——POS 與線上點餐共用的讀取形狀。"""

    item: MenuItem
    category: MenuCategory | None
    option_groups: list[OptionGroupDetail]


@dataclass(frozen=True)
class MenuSelection:
    """一行餐飲的計價結果：單價（基本價＋選項加價）、帶選項的品名、選項快照。"""

    unit_price: Decimal
    description: str
    options_snapshot: list[dict[str, object]]


def _line_description(name: str, option_names: Sequence[str]) -> str:
    text = name if not option_names else f"{name}（{'、'.join(option_names)}）"
    if len(text) <= SALE_LINE_DESCRIPTION_MAX:
        return text
    return text[: SALE_LINE_DESCRIPTION_MAX - 1] + "…"


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
        description: str | None = None,
        sort_order: int = 0,
        actor_user_id: int,
    ) -> MenuItem:
        """建立品項。`category` 是分類**名稱**：同名沿用既有分類，沒有就建一個。"""
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
                category_id=await self._category_id_for(store_id, category),
                description=description,
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
        description: str | None | object = _UNSET,
        sort_order: int | None = None,
        is_available: bool | None = None,
        actor_user_id: int,
    ) -> MenuItem:
        """部分更新（None=不變；category/description 另以 _UNSET 區分「不變」與「清空」）。

        改價寫稽核。`category` 為分類名稱，沿用或新建分類（同 create）。
        """
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
            item.category_id = await self._category_id_for(
                store_id,
                category,  # type: ignore[arg-type]
            )
        if description is not _UNSET:
            item.description = description  # type: ignore[assignment]
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

    async def describe_items(
        self, store_id: int, items: Sequence[MenuItem]
    ) -> list[MenuItemDetail]:
        """補上分類與所掛群組（含未封存選項）；固定幾次查詢，不隨品項數成長。"""
        categories = {
            c.id: c
            for c in await self._repo.get_categories(
                store_id, sorted({i.category_id for i in items if i.category_id is not None})
            )
        }
        links = await self._repo.item_group_links([i.id for i in items])
        group_ids = sorted({link.group_id for link in links})
        groups = {g.id: g for g in await self._repo.get_groups(store_id, group_ids)}
        options_by_group: dict[int, list[MenuOption]] = {gid: [] for gid in group_ids}
        for option in await self._repo.list_options(group_ids):
            options_by_group[option.group_id].append(option)
        groups_by_item: dict[int, list[OptionGroupDetail]] = {i.id: [] for i in items}
        for link in links:
            group = groups.get(link.group_id)
            if group is not None:
                groups_by_item[link.item_id].append(
                    OptionGroupDetail(group=group, options=options_by_group[group.id])
                )
        return [
            MenuItemDetail(
                item=i,
                category=categories.get(i.category_id) if i.category_id is not None else None,
                option_groups=groups_by_item[i.id],
            )
            for i in items
        ]

    async def price_selection(
        self, store_id: int, item: MenuItem, option_ids: Sequence[int]
    ) -> MenuSelection:
        """依菜單驗證所選選項並計價（docs/44 §3.2–3.3）。永遠以後端菜單為準，不信任客戶端金額。

        - 選項必須屬於品項目前所掛、未封存的群組，且未封存；停售 → MenuItemUnavailable。
        - 每個群組所選數量須在 [min_select, max_select]；同一選項不可重複。
        - 品名依「群組掛載順序 → 群組內選項順序」排列，與客戶端送來的順序無關。
        """
        if len(set(option_ids)) != len(option_ids):
            raise SaleLineInvalid(f"「{item.name}」的同一個選項不能選兩次")
        details = (await self.describe_items(store_id, [item]))[0].option_groups
        chosen = set(option_ids)
        known = {o.id for d in details for o in d.options}
        if not chosen <= known:
            raise SaleLineInvalid(f"「{item.name}」沒有這個選項，請重新選擇")
        unit_price = item.unit_price
        names: list[str] = []
        snapshot: list[dict[str, object]] = []
        for detail in details:
            group = detail.group
            picked = [o for o in detail.options if o.id in chosen]
            if len(picked) < group.min_select:
                if group.min_select == 1:
                    raise SaleLineInvalid(f"「{item.name}」要選「{group.name}」")
                raise SaleLineInvalid(f"「{group.name}」至少要選 {group.min_select} 項")
            if len(picked) > group.max_select:
                raise SaleLineInvalid(f"「{group.name}」最多只能選 {group.max_select} 項")
            for option in picked:
                if not option.is_available:
                    raise MenuItemUnavailable(f"「{option.name}」目前停售")
                unit_price += option.price_delta
                names.append(option.name)
                snapshot.append(
                    {
                        "group_id": group.id,
                        "group": group.name,
                        "option_id": option.id,
                        "option": option.name,
                        "price_delta": format_ntd(option.price_delta),
                    }
                )
        return MenuSelection(
            unit_price=unit_price,
            description=_line_description(item.name, names),
            options_snapshot=snapshot,
        )

    async def set_item_option_groups(
        self, store_id: int, item_id: int, group_ids: Sequence[int], *, actor_user_id: int
    ) -> MenuItem:
        """整批替換品項所掛的群組（順序即顯示順序）。掛群組會改變可點的價格 → 寫稽核。"""
        item = await self._repo.get_for_update(store_id, item_id)
        if item is None or item.archived_at is not None:
            raise MenuItemNotFound(f"找不到菜單品項 {item_id}")
        if len(set(group_ids)) != len(group_ids):
            raise SaleLineInvalid("同一個選項群組不能重複掛在同一個品項上")
        found = await self._repo.get_groups(store_id, group_ids)
        if len(found) != len(group_ids):
            raise MenuEntryNotFound("找不到指定的選項群組")
        before = [link.group_id for link in await self._repo.item_group_links([item_id])]
        await self._repo.replace_item_groups(store_id, item_id, group_ids)
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="SET_MENU_ITEM_OPTION_GROUPS",
            entity_type="menu_item",
            entity_id=str(item_id),
            before={"group_ids": before},
            after={"group_ids": list(group_ids)},
        )
        return item

    # ── 分類 ──

    async def _category_id_for(self, store_id: int, name: str | None) -> int | None:
        """分類名稱 → id；空白＝未分類；沒有就建立（POS 管理頁「打字即建」）。"""
        if name is None or not name.strip():
            return None
        name = name.strip()
        existing = await self._repo.find_category_by_name(store_id, name)
        if existing is not None:
            return existing.id
        return (await self._repo.add(MenuCategory(store_id=store_id, name=name))).id

    async def list_categories(self, store_id: int) -> list[MenuCategory]:
        return await self._repo.list_categories(store_id)

    async def create_category(
        self, store_id: int, *, name: str, sort_order: int = 0
    ) -> MenuCategory:
        if await self._repo.find_category_by_name(store_id, name) is not None:
            raise DuplicateMenuEntry(f"已有同名分類：{name}")
        return await self._repo.add(
            MenuCategory(store_id=store_id, name=name, sort_order=sort_order)
        )

    async def update_category(
        self,
        store_id: int,
        category_id: int,
        *,
        name: str | None = None,
        sort_order: int | None = None,
    ) -> MenuCategory:
        category = await self._repo.get_category(store_id, category_id, for_update=True)
        if category is None:
            raise MenuEntryNotFound(f"找不到分類 {category_id}")
        if name is not None and name != category.name:
            if await self._repo.find_category_by_name(store_id, name) is not None:
                raise DuplicateMenuEntry(f"已有同名分類：{name}")
            category.name = name
        if sort_order is not None:
            category.sort_order = sort_order
        await self._session.flush()
        return category

    async def archive_category(self, store_id: int, category_id: int) -> MenuCategory:
        """封存分類；底下品項改為未分類（品項本身不受影響）。"""
        category = await self._repo.get_category(store_id, category_id, for_update=True)
        if category is None:
            raise MenuEntryNotFound(f"找不到分類 {category_id}")
        category.archived_at = datetime.now(UTC)
        await self._repo.clear_category(store_id, category_id)
        return category

    # ── 選項群組／選項 ──

    async def list_option_groups(self, store_id: int) -> list[OptionGroupDetail]:
        groups = await self._repo.list_groups(store_id)
        return await self._with_options(groups)

    async def get_option_group(self, store_id: int, group_id: int) -> OptionGroupDetail:
        group = await self._repo.get_group(store_id, group_id)
        if group is None:
            raise MenuEntryNotFound(f"找不到選項群組 {group_id}")
        return (await self._with_options([group]))[0]

    async def _with_options(self, groups: Sequence[MenuOptionGroup]) -> list[OptionGroupDetail]:
        by_group: dict[int, list[MenuOption]] = {g.id: [] for g in groups}
        for option in await self._repo.list_options(list(by_group)):
            by_group[option.group_id].append(option)
        return [OptionGroupDetail(group=g, options=by_group[g.id]) for g in groups]

    async def create_option_group(
        self,
        store_id: int,
        *,
        name: str,
        min_select: int,
        max_select: int,
        options: Sequence[tuple[str, Decimal]] = (),
        sort_order: int = 0,
        actor_user_id: int,
    ) -> OptionGroupDetail:
        _validate_bounds(min_select, max_select)
        names = [n for n, _ in options]
        if len(set(names)) != len(names):
            raise DuplicateMenuEntry("同一個群組裡的選項名稱不能重複")
        for _, delta in options:
            _validate_price_delta(delta)
        if await self._repo.group_name_exists(store_id, name):
            raise DuplicateMenuEntry(f"已有同名選項群組：{name}")
        group = await self._repo.add(
            MenuOptionGroup(
                store_id=store_id,
                name=name,
                min_select=min_select,
                max_select=max_select,
                sort_order=sort_order,
            )
        )
        for i, (opt_name, delta) in enumerate(options):
            await self._repo.add(
                MenuOption(
                    store_id=store_id,
                    group_id=group.id,
                    name=opt_name,
                    price_delta=delta,
                    sort_order=i,
                )
            )
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="CREATE_MENU_OPTION_GROUP",
            entity_type="menu_option_group",
            entity_id=str(group.id),
            after={"name": name, "options": [[n, str(d)] for n, d in options]},
        )
        return await self.get_option_group(store_id, group.id)

    async def update_option_group(
        self,
        store_id: int,
        group_id: int,
        *,
        name: str | None = None,
        min_select: int | None = None,
        max_select: int | None = None,
        sort_order: int | None = None,
    ) -> OptionGroupDetail:
        group = await self._repo.get_group(store_id, group_id, for_update=True)
        if group is None:
            raise MenuEntryNotFound(f"找不到選項群組 {group_id}")
        new_min = group.min_select if min_select is None else min_select
        new_max = group.max_select if max_select is None else max_select
        _validate_bounds(new_min, new_max)
        if name is not None and name != group.name:
            if await self._repo.group_name_exists(store_id, name, exclude_id=group_id):
                raise DuplicateMenuEntry(f"已有同名選項群組：{name}")
            group.name = name
        group.min_select, group.max_select = new_min, new_max
        if sort_order is not None:
            group.sort_order = sort_order
        await self._session.flush()
        return await self.get_option_group(store_id, group_id)

    async def archive_option_group(self, store_id: int, group_id: int) -> MenuOptionGroup:
        """封存群組：所有品項上都不再出現（掛載紀錄保留，歷史收據靠 sale_line 快照）。"""
        group = await self._repo.get_group(store_id, group_id, for_update=True)
        if group is None:
            raise MenuEntryNotFound(f"找不到選項群組 {group_id}")
        group.archived_at = datetime.now(UTC)
        await self._session.flush()
        return group

    async def add_option(
        self,
        store_id: int,
        group_id: int,
        *,
        name: str,
        price_delta: Decimal,
        actor_user_id: int,
    ) -> MenuOption:
        _validate_price_delta(price_delta)
        group = await self._repo.get_group(store_id, group_id, for_update=True)
        if group is None:
            raise MenuEntryNotFound(f"找不到選項群組 {group_id}")
        if await self._repo.option_name_exists(group_id, name):
            raise DuplicateMenuEntry(f"「{group.name}」已有選項：{name}")
        option = await self._repo.add(
            MenuOption(
                store_id=store_id,
                group_id=group_id,
                name=name,
                price_delta=price_delta,
                sort_order=await self._repo.next_option_sort(group_id),
            )
        )
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="CREATE_MENU_OPTION",
            entity_type="menu_option",
            entity_id=str(option.id),
            after={"group_id": group_id, "name": name, "price_delta": str(price_delta)},
        )
        return option

    async def update_option(
        self,
        store_id: int,
        option_id: int,
        *,
        name: str | None = None,
        price_delta: Decimal | None = None,
        is_available: bool | None = None,
        sort_order: int | None = None,
        actor_user_id: int,
    ) -> MenuOption:
        option = await self._repo.get_option(store_id, option_id, for_update=True)
        if option is None:
            raise MenuEntryNotFound(f"找不到選項 {option_id}")
        before_delta = option.price_delta
        if name is not None and name != option.name:
            if await self._repo.option_name_exists(option.group_id, name, exclude_id=option_id):
                raise DuplicateMenuEntry(f"同群組已有選項：{name}")
            option.name = name
        if price_delta is not None:
            _validate_price_delta(price_delta)
            option.price_delta = price_delta
        if is_available is not None:
            option.is_available = is_available
        if sort_order is not None:
            option.sort_order = sort_order
        await self._session.flush()
        if price_delta is not None and price_delta != before_delta:
            await write_audit_log(
                self._session,
                store_id=store_id,
                actor_user_id=actor_user_id,
                action="UPDATE_MENU_OPTION_PRICE",
                entity_type="menu_option",
                entity_id=str(option_id),
                before={"price_delta": format_ntd(before_delta)},
                after={"price_delta": format_ntd(price_delta)},
            )
        return option

    async def archive_option(self, store_id: int, option_id: int) -> MenuOption:
        option = await self._repo.get_option(store_id, option_id, for_update=True)
        if option is None:
            raise MenuEntryNotFound(f"找不到選項 {option_id}")
        option.archived_at = datetime.now(UTC)
        await self._session.flush()
        return option
