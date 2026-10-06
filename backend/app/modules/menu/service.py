"""menu 業務邏輯：餐飲菜單品項、分類、選項群組與選項（docs/44 §3）。

本層只 flush、不 commit（由呼叫端控制）。改價（含選項加價）屬敏感操作 → 寫 audit_log（§5）。
金額為含稅整數元（§6）：unit_price 必須為正整數元；選項 price_delta 為 0 以上整數元。
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import MAX_NTD, format_ntd
from app.core.time import store_date, utc_now
from app.modules.menu.models import (
    MenuCategory,
    MenuItem,
    MenuOption,
    MenuOptionGroup,
    MenuStockAdjustment,
)
from app.modules.menu.photos import process_photo_async
from app.modules.menu.repository import MenuRepository
from app.shared.enums import MenuStockAdjustReason, MenuStockTarget
from app.shared.exceptions import (
    DuplicateMenuEntry,
    DuplicateMenuItem,
    InsufficientStock,
    ItemDeleteBlocked,
    MenuEntryNotFound,
    MenuItemNotFound,
    MenuItemUnavailable,
    MenuStockConflict,
    SaleLineInvalid,
)

# 銷售明細品名欄寬（sale_lines.description）；品名＋選項超過就截斷並以「…」結尾。
SALE_LINE_DESCRIPTION_MAX: Final = 300

# 區分「未提供（不變）」與「明確設為 None（清空）」——目前僅 category 需要清空語意。
_UNSET: Final = object()
_PHOTO_KEY: Final = re.compile(r"[0-9a-f]{64}")


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
    """一行餐飲的計價結果：單價（基本價＋選項加價）、帶選項的品名、選項快照、所選選項。"""

    unit_price: Decimal
    description: str
    options_snapshot: list[dict[str, object]]
    options: list[MenuOption]
    # 一份的成本（docs/49 §3）：品項成本＋Σ 選項成本（沒填的選項算 0）；品項沒填成本＝未知。
    unit_cost: Decimal | None = None


@dataclass(frozen=True)
class DailyStockEntry:
    """一個每日限量對象今天的狀態（開店檢查與 POS 共用）。"""

    kind: MenuStockTarget
    id: int
    label: str
    remaining: int
    set_today: bool


def today() -> date:
    """門市營業日（台北）。每日限量以此判斷「是不是今天填的」。"""
    return store_date(utc_now())


def remaining_today(entry: MenuItem | MenuOption, day: date) -> int | None:
    """今天還能賣幾份；不限量 → None。stock_day 不是今天＝已歸零（不靠排程）。"""
    if not entry.daily_limited:
        return None
    if entry.stock_day != day or entry.stock_qty is None:
        return 0
    return entry.stock_qty


def _short(entry: MenuItem | MenuOption, day: date, qty: int) -> bool:
    left = remaining_today(entry, day)
    return left is not None and left < qty


def _adjust_reason(delta: int, reason: MenuStockAdjustReason | str | None) -> MenuStockAdjustReason:
    """加＝補貨；減＝必須是報廢或盤點校正（往上校正請用「改成」）。"""
    if delta == 0:
        raise SaleLineInvalid("加減的份數不可為 0")
    if delta > 0:
        if reason not in (None, MenuStockAdjustReason.RESTOCK):
            raise SaleLineInvalid("增加份數的原因只能是補貨")
        return MenuStockAdjustReason.RESTOCK
    if reason is None or MenuStockAdjustReason(reason) is MenuStockAdjustReason.RESTOCK:
        raise SaleLineInvalid("減少份數要選原因：報廢或盤點校正")
    return MenuStockAdjustReason(reason)


def _shortage_message(name: str, entry: MenuItem | MenuOption, day: date) -> str:
    left = remaining_today(entry, day) or 0
    if entry.stock_day != day:
        return f"「{name}」今天還沒設定數量（每日限量），請先到開店檢查填份數"
    if left == 0:
        return f"「{name}」今天已售完"
    return f"「{name}」今天只剩 {left} 份"


def _line_description(name: str, picked: Sequence[tuple[str, str]]) -> str:
    """品名＋選項。平常只列選項名（短，收據好讀）；所選選項有同名時（甜度「正常」、
    冰量「正常」），撞名的那幾項改成「群組名＋選項名」，否則分不出哪個是哪個。"""
    counts: dict[str, int] = {}
    for _, option in picked:
        counts[option] = counts.get(option, 0) + 1
    labels = [option if counts[option] == 1 else f"{group}{option}" for group, option in picked]
    text = name if not labels else f"{name}（{'、'.join(labels)}）"
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

    async def set_item_photo(
        self, store_id: int, item_id: int, data: bytes, *, actor_user_id: int
    ) -> MenuItem:
        """上傳品項照片（docs/44 §3.4）：轉 WebP、去 EXIF、依內容雜湊去重，換照片寫稽核。

        不合格的檔案丟 `MenuPhotoInvalid`，品項不動。
        """
        # 先轉檔（背景執行緒、不持鎖），轉好才鎖品項列寫入：轉檔約一秒，不該讓別人等這把鎖。
        photo = await process_photo_async(data)
        item = await self._repo.get_for_update(store_id, item_id)
        if item is None or item.archived_at is not None:
            raise MenuItemNotFound(f"找不到菜單品項 {item_id}")
        await self._repo.save_photo(
            store_id,
            sha256=photo.sha256,
            content=photo.content,
            width=photo.width,
            height=photo.height,
        )
        await self._change_photo(store_id, item, photo.sha256, actor_user_id)
        return item

    async def clear_item_photo(
        self, store_id: int, item_id: int, *, actor_user_id: int
    ) -> MenuItem:
        """移除品項照片（照片本身保留，已發佈的線上菜單可能還在引用）。"""
        item = await self._repo.get_for_update(store_id, item_id)
        if item is None or item.archived_at is not None:
            raise MenuItemNotFound(f"找不到菜單品項 {item_id}")
        await self._change_photo(store_id, item, None, actor_user_id)
        return item

    async def _change_photo(
        self, store_id: int, item: MenuItem, sha256: str | None, actor_user_id: int
    ) -> None:
        before = item.photo_sha256
        if before == sha256:
            return
        item.photo_sha256 = sha256
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="UPDATE_MENU_ITEM_PHOTO",
            entity_type="menu_item",
            entity_id=str(item.id),
            before={"photo_sha256": before},
            after={"photo_sha256": sha256},
        )

    async def photo_content(self, sha256: str) -> bytes | None:
        """公開讀取照片（線上菜單、POS 磚）。雜湊格式不對直接當找不到。"""
        if _PHOTO_KEY.fullmatch(sha256) is None:
            return None
        return await self._repo.photo_content(sha256)

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
        daily_limited: bool | None = None,
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
        await self._set_daily_limited(store_id, item, daily_limited, actor_user_id, "menu_item")
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

    async def _set_daily_limited(
        self,
        store_id: int,
        target: MenuItem | MenuOption,
        daily_limited: bool | None,
        actor_user_id: int,
        entity_type: str,
    ) -> None:
        """開／關每日限量（管理者設定，寫稽核）。關掉＝不限量；重新打開＝今天要重新填份數。"""
        if daily_limited is None or daily_limited == target.daily_limited:
            return
        target.daily_limited = daily_limited
        target.stock_qty = None
        target.stock_day = None
        target.stock_generation += 1
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="UPDATE_MENU_DAILY_LIMITED",
            entity_type=entity_type,
            entity_id=str(target.id),
            before={"daily_limited": not daily_limited},
            after={"daily_limited": daily_limited},
        )

    # ── 查詢 ──
    async def get(
        self, store_id: int, item_id: int, *, for_update: bool = False
    ) -> MenuItem | None:
        """Read a store-scoped item, optionally locking it for coordinated updates."""
        if for_update:
            return await self._repo.get_for_update(store_id, item_id)
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
        self,
        store_id: int,
        item: MenuItem,
        option_ids: Sequence[int],
        qty: int = 1,
        *,
        check_stock: bool = True,
    ) -> MenuSelection:
        """依菜單驗證所選選項並計價（docs/44 §3.2–3.3）。永遠以後端菜單為準，不信任客戶端金額。

        `check_stock=False`：只算價、不看今天剩幾份——線上單帶入結帳前的預覽用，那張單保留的
        份數已經先扣掉了，用剩餘份數判斷會誤報售完（真正結帳仍會檢查）。

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
        day = today()
        if check_stock and _short(item, day, qty):
            raise InsufficientStock(_shortage_message(item.name, item, day))
        unit_price = item.unit_price
        chosen_options: list[MenuOption] = []
        picked_names: list[tuple[str, str]] = []
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
                if check_stock and _short(option, day, qty):
                    raise InsufficientStock(_shortage_message(option.name, option, day))
                chosen_options.append(option)
                unit_price += option.price_delta
                picked_names.append((group.name, option.name))
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
            description=_line_description(item.name, picked_names),
            options_snapshot=snapshot,
            options=chosen_options,
            unit_cost=(
                None
                if item.unit_cost is None
                else item.unit_cost
                + sum((o.unit_cost or Decimal(0) for o in chosen_options), Decimal(0))
            ),
        )

    # ── 每日限量（docs/44 §3.7）──

    async def consume_daily_stock(
        self, store_id: int, item: MenuItem, selection: MenuSelection, qty: int
    ) -> list[dict[str, object]]:
        """結帳扣份數（原子）；不夠 → InsufficientStock，同一交易的其他扣減會一起回滾。

        回傳扣到的對象與份數版本（存進明細的 `menu_stock_consumed`，作廢加回時核對）。
        """
        day = today()
        consumed: list[dict[str, object]] = []
        targets: list[tuple[MenuStockTarget, MenuItem | MenuOption]] = [
            (MenuStockTarget.ITEM, item),
            *((MenuStockTarget.OPTION, option) for option in selection.options),
        ]
        for kind, entry in targets:
            # 鎖住該列重讀再判斷是否限量：先前讀到的旗標可能已過期——管理者剛好在這筆結帳中途
            # 把它切成限量時，憑舊旗標跳過扣減會讓這筆一份都沒扣就成交（Codex 對抗審查 O1c）。
            # 切換限量走同一列的鎖，兩邊因此排隊、不會交錯。
            await self._session.refresh(entry, with_for_update=True)
            if not entry.daily_limited:
                continue
            generation = await self._repo.consume_stock(type(entry), store_id, entry.id, qty, day)
            await self._session.refresh(entry)
            if generation is None:
                raise InsufficientStock(_shortage_message(entry.name, entry, day))
            consumed.append(
                {
                    "kind": kind.value,
                    "id": entry.id,
                    "generation": generation,
                    "day": day.isoformat(),
                }
            )
        return consumed

    async def restore_daily_stock(
        self, store_id: int, consumed: Sequence[dict[str, object]], *, qty: int
    ) -> None:
        """作廢加回份數：只在**同一個營業日**、且份數版本沒變（賣出後沒人重設過）才加回。

        昨天賣掉的不會變成今天的份數；賣出後店員按過「改成」＝已實際數過，不再加回。
        """
        day = today()
        for entry in consumed:
            if str(entry["day"]) != day.isoformat():
                continue
            model = MenuItem if entry["kind"] == MenuStockTarget.ITEM.value else MenuOption
            await self._repo.restore_stock(
                model,
                store_id,
                int(str(entry["id"])),
                qty,
                int(str(entry["generation"])),
                day,
            )

    async def _stock_target(
        self, store_id: int, kind: MenuStockTarget, target_id: int
    ) -> tuple[MenuItem | MenuOption, str]:
        if kind is MenuStockTarget.ITEM:
            item = await self._repo.get(store_id, target_id)
            if item is None or item.archived_at is not None:
                raise MenuItemNotFound(f"找不到菜單品項 {target_id}")
            return item, item.name
        option = await self._repo.get_option(store_id, target_id)
        if option is None:
            raise MenuEntryNotFound(f"找不到選項 {target_id}")
        group = await self._repo.get_group(store_id, option.group_id)
        label = option.name if group is None else f"{group.name}：{option.name}"
        return option, label

    def _entry(
        self, kind: MenuStockTarget, target: MenuItem | MenuOption, label: str
    ) -> DailyStockEntry:
        day = today()
        return DailyStockEntry(
            kind=kind,
            id=target.id,
            label=label,
            remaining=remaining_today(target, day) or 0,
            set_today=target.stock_day == day,
        )

    async def remaining(
        self, store_id: int, kind: MenuStockTarget | str, target_id: int
    ) -> int | None:
        target, _ = await self._stock_target(store_id, MenuStockTarget(kind), target_id)
        await self._session.refresh(target)
        return remaining_today(target, today())

    async def set_daily_stock(
        self,
        store_id: int,
        kind: MenuStockTarget | str,
        target_id: int,
        *,
        qty: int,
        expected_remaining: int,
        actor_user_id: int,
    ) -> DailyStockEntry:
        """把今天的份數改成 qty（開店填數量、或營業中直接改）。

        必須附上店員畫面上看到的數字：期間若有人結帳、數字已變，就拒絕讓店員重看，
        不能把剛賣掉的份數覆寫回來。
        """
        kind = MenuStockTarget(kind)
        if qty < 0:
            raise SaleLineInvalid("份數不可小於 0")
        target, label = await self._stock_target(store_id, kind, target_id)
        if not target.daily_limited:
            raise MenuStockConflict(f"「{label}」是不限量品項，不需要設定份數")
        day = today()
        result = await self._repo.set_stock(
            type(target), store_id, target_id, qty, expected_remaining, day
        )
        await self._session.refresh(target)
        if result is None:
            now = remaining_today(target, day) or 0
            raise MenuStockConflict(f"「{label}」的數量剛剛變動（現在剩 {now} 份），請重新確認")
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="SET_MENU_DAILY_STOCK",
            entity_type=f"menu_{kind.value}",
            entity_id=str(target_id),
            before={"remaining": expected_remaining},
            after={"remaining": qty, "day": day.isoformat()},
        )
        return self._entry(kind, target, label)

    async def adjust_daily_stock(
        self,
        store_id: int,
        kind: MenuStockTarget | str,
        target_id: int,
        *,
        delta: int,
        actor_user_id: int,
        reason: MenuStockAdjustReason | str | None = None,
    ) -> DailyStockEntry:
        """今天的份數加減（剛做好 +4、報廢 −1）。原子操作，與結帳同時進行也不會算錯。

        加：原因固定是補貨（可省略）。減：必須說明是報廢還是盤點校正——報廢統計靠這個。
        """
        kind = MenuStockTarget(kind)
        resolved = _adjust_reason(delta, reason)
        target, label = await self._stock_target(store_id, kind, target_id)
        if not target.daily_limited:
            raise MenuStockConflict(f"「{label}」是不限量品項，不需要設定份數")
        day = today()
        result = await self._repo.add_stock(type(target), store_id, target_id, delta, day)
        await self._session.refresh(target)
        if result is None:
            now = remaining_today(target, day) or 0
            raise MenuStockConflict(f"「{label}」現在剩 {now} 份，不能再減 {-delta} 份")
        await self._repo.add_adjustment(
            MenuStockAdjustment(
                store_id=store_id,
                target_kind=kind.value,
                target_id=target_id,
                delta=delta,
                reason=resolved.value,
                business_date=day,
                unit_cost_snapshot=target.unit_cost,
                actor_user_id=actor_user_id,
            )
        )
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="ADJUST_MENU_DAILY_STOCK",
            entity_type=f"menu_{kind.value}",
            entity_id=str(target_id),
            before={"remaining": result - delta},
            after={"remaining": result, "day": day.isoformat(), "reason": resolved.value},
        )
        return self._entry(kind, target, label)

    async def waste_summary(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> list[tuple[MenuStockAdjustReason, int, Decimal, int]]:
        """期間內報廢／盤點短少：(原因, 份數, 已知成本合計, 成本未知份數)（docs/49 §4）。

        歸屬按下的那一刻（與退貨扣減同口徑，以時間區間切，不是營業日）。補貨不算。
        """
        return await self._repo.waste_summary(store_id, date_from, date_to)

    async def list_daily_stock(self, store_id: int) -> list[DailyStockEntry]:
        """今天要填份數的對象（每日限量、未封存、未停售）：品項在前、選項在後。"""
        entries = [
            self._entry(MenuStockTarget.ITEM, item, item.name)
            for item in await self._repo.list_limited_items(store_id)
        ]
        entries += [
            self._entry(MenuStockTarget.OPTION, option, f"{group.name}：{option.name}")
            for option, group in await self._repo.list_limited_options(store_id)
        ]
        return entries

    async def daily_stock_pending(self, store_id: int) -> int:
        """今天還沒填份數的限量對象數（開店前檢查用）。填 0 也算填過。"""
        return sum(1 for e in await self.list_daily_stock(store_id) if not e.set_today)

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
        daily_limited: bool | None = None,
        # 成本沿用 _UNSET 慣例：沒提供＝不變，明確給 None＝清空。
        unit_cost: Decimal | None | object = _UNSET,
        actor_user_id: int,
    ) -> MenuOption:
        option = await self._repo.get_option(store_id, option_id, for_update=True)
        if option is None:
            raise MenuEntryNotFound(f"找不到選項 {option_id}")
        before_delta = option.price_delta
        before_cost = option.unit_cost
        if unit_cost is not _UNSET:
            _validate_cost(unit_cost)  # type: ignore[arg-type]
            option.unit_cost = unit_cost  # type: ignore[assignment]
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
        await self._set_daily_limited(store_id, option, daily_limited, actor_user_id, "menu_option")
        await self._session.flush()
        if unit_cost is not _UNSET and unit_cost != before_cost:
            await write_audit_log(
                self._session,
                store_id=store_id,
                actor_user_id=actor_user_id,
                action="UPDATE_MENU_OPTION_COST",
                entity_type="menu_option",
                entity_id=str(option_id),
                before={"unit_cost": None if before_cost is None else format_ntd(before_cost)},
                after={
                    "unit_cost": None
                    if unit_cost is None
                    else format_ntd(unit_cost)  # type: ignore[arg-type]
                },
            )
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
