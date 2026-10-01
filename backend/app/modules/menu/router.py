"""menu 路由：餐飲菜單品項 CRUD（docs/10）。

讀取（POS 取菜單磚）開放給任何登入者；新增/改價/上下架/封存限 MANAGER（§管理權限）。
只做 I/O 與驗證；業務邏輯在 service。領域例外對應 HTTP：NotFound→404、Duplicate→409、
售價不合法→422。寫入端點成功才 commit（get_session 不自動 commit）。
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.menu.models import MenuItem
from app.modules.menu.schemas import (
    DailyStockAdjustRequest,
    DailyStockEntryRead,
    DailyStockSetRequest,
    MenuCategoryCreateRequest,
    MenuCategoryRead,
    MenuCategoryUpdateRequest,
    MenuItemCreateRequest,
    MenuItemOptionGroupsRequest,
    MenuItemRead,
    MenuItemUpdateRequest,
    MenuOptionGroupCreateRequest,
    MenuOptionGroupRead,
    MenuOptionGroupUpdateRequest,
    MenuOptionInput,
    MenuOptionRead,
    MenuOptionUpdateRequest,
)
from app.modules.menu.service import MenuService
from app.shared.enums import MenuStockTarget
from app.shared.exceptions import (
    DuplicateMenuEntry,
    DuplicateMenuItem,
    ItemDeleteBlocked,
    MenuEntryNotFound,
    MenuItemNotFound,
    MenuStockConflict,
    SaleLineInvalid,
)

router = APIRouter(prefix="/menu-items", tags=["menu"])
# 分類／選項群組／選項掛在各自的路徑下，同一個 router 檔維持 menu 模組單一入口。
entries_router = APIRouter(tags=["menu"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role("MANAGER"))]


@router.get("", response_model=list[MenuItemRead], operation_id="listMenuItems")
async def list_menu_items(
    session: SessionDep,
    user: AuthDep,
    available_only: Annotated[bool, Query()] = False,
) -> list[MenuItemRead]:
    svc = MenuService(session)
    items = await svc.list_items(user.store_id, include_unavailable=not available_only)
    return [MenuItemRead.from_detail(d) for d in await svc.describe_items(user.store_id, items)]


@router.post(
    "",
    response_model=MenuItemRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMenuItem",
)
async def create_menu_item(
    body: MenuItemCreateRequest, session: SessionDep, user: ManagerDep
) -> MenuItemRead:
    try:
        item = await MenuService(session).create_menu_item(
            user.store_id,
            name=body.name,
            unit_price=body.unit_price,
            unit_cost=body.unit_cost,
            category=body.category,
            description=body.description,
            sort_order=body.sort_order,
            actor_user_id=user.id,
        )
    except SaleLineInvalid as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except DuplicateMenuItem as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return await _read_item(session, item)


@router.patch("/{item_id}", response_model=MenuItemRead, operation_id="updateMenuItem")
async def update_menu_item(
    item_id: int, body: MenuItemUpdateRequest, session: SessionDep, user: ManagerDep
) -> MenuItemRead:
    # category 以 model_fields_set 區分「未提供（不變）」與「明確 null（清空）」：
    # 只在有提供時才傳 category，否則交給 service 預設 sentinel（不變）。
    category_kw: dict[str, str | None] = (
        {"category": body.category} if "category" in body.model_fields_set else {}
    )
    # 成本同理：明確送 null＝清空（不知道成本），沒送＝不變。
    cost_kw: dict[str, Decimal | None] = (
        {"unit_cost": body.unit_cost} if "unit_cost" in body.model_fields_set else {}
    )
    description_kw: dict[str, str | None] = (
        {"description": body.description} if "description" in body.model_fields_set else {}
    )
    try:
        item = await MenuService(session).update_menu_item(
            user.store_id,
            item_id,
            name=body.name,
            unit_price=body.unit_price,
            sort_order=body.sort_order,
            is_available=body.is_available,
            daily_limited=body.daily_limited,
            actor_user_id=user.id,
            **category_kw,
            **cost_kw,
            **description_kw,
        )
    except MenuItemNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SaleLineInvalid as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except DuplicateMenuItem as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return await _read_item(session, item)


@router.delete("/{item_id}", response_model=MenuItemRead, operation_id="archiveMenuItem")
async def archive_menu_item(item_id: int, session: SessionDep, user: ManagerDep) -> MenuItemRead:
    try:
        item = await MenuService(session).archive_menu_item(
            user.store_id, item_id, actor_user_id=user.id
        )
    except MenuItemNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await session.commit()
    return await _read_item(session, item)


@router.delete(
    "/{item_id}/delete",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteMenuItem",
)
async def delete_menu_item(item_id: int, session: SessionDep, user: ManagerDep) -> None:
    """真刪誤建的品項（沒賣過才行）；賣過的回 409，畫面改提供下架。"""
    try:
        deleted = await MenuService(session).delete_menu_item(
            user.store_id, item_id, actor_user_id=user.id
        )
    except ItemDeleteBlocked as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到菜單品項")
    await session.commit()


async def _read_item(session: AsyncSession, item: MenuItem) -> MenuItemRead:
    [detail] = await MenuService(session).describe_items(item.store_id, [item])
    return MenuItemRead.from_detail(detail)


def _http_error(exc: Exception) -> HTTPException:
    """菜單領域例外 → HTTP（找不到 404、重複 409、不合法 422）。"""
    if isinstance(exc, MenuEntryNotFound | MenuItemNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, DuplicateMenuEntry | MenuStockConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


_MenuErrors = (
    MenuEntryNotFound,
    MenuItemNotFound,
    DuplicateMenuEntry,
    MenuStockConflict,
    SaleLineInvalid,
)


@router.put(
    "/{item_id}/option-groups",
    response_model=MenuItemRead,
    operation_id="setMenuItemOptionGroups",
)
async def set_menu_item_option_groups(
    item_id: int, body: MenuItemOptionGroupsRequest, session: SessionDep, user: ManagerDep
) -> MenuItemRead:
    try:
        item = await MenuService(session).set_item_option_groups(
            user.store_id, item_id, body.group_ids, actor_user_id=user.id
        )
    except _MenuErrors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return await _read_item(session, item)


# ── 分類 ──


@entries_router.get(
    "/menu-categories", response_model=list[MenuCategoryRead], operation_id="listMenuCategories"
)
async def list_menu_categories(session: SessionDep, user: AuthDep) -> list[MenuCategoryRead]:
    return [
        MenuCategoryRead.from_model(c)
        for c in await MenuService(session).list_categories(user.store_id)
    ]


@entries_router.post(
    "/menu-categories",
    response_model=MenuCategoryRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMenuCategory",
)
async def create_menu_category(
    body: MenuCategoryCreateRequest, session: SessionDep, user: ManagerDep
) -> MenuCategoryRead:
    try:
        category = await MenuService(session).create_category(
            user.store_id, name=body.name, sort_order=body.sort_order
        )
    except _MenuErrors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return MenuCategoryRead.from_model(category)


@entries_router.patch(
    "/menu-categories/{category_id}",
    response_model=MenuCategoryRead,
    operation_id="updateMenuCategory",
)
async def update_menu_category(
    category_id: int, body: MenuCategoryUpdateRequest, session: SessionDep, user: ManagerDep
) -> MenuCategoryRead:
    try:
        category = await MenuService(session).update_category(
            user.store_id, category_id, name=body.name, sort_order=body.sort_order
        )
    except _MenuErrors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return MenuCategoryRead.from_model(category)


@entries_router.delete(
    "/menu-categories/{category_id}",
    response_model=MenuCategoryRead,
    operation_id="archiveMenuCategory",
)
async def archive_menu_category(
    category_id: int, session: SessionDep, user: ManagerDep
) -> MenuCategoryRead:
    try:
        category = await MenuService(session).archive_category(user.store_id, category_id)
    except _MenuErrors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return MenuCategoryRead.from_model(category)


# ── 選項群組 ──


@entries_router.get(
    "/menu-option-groups",
    response_model=list[MenuOptionGroupRead],
    operation_id="listMenuOptionGroups",
)
async def list_menu_option_groups(session: SessionDep, user: AuthDep) -> list[MenuOptionGroupRead]:
    groups = await MenuService(session).list_option_groups(user.store_id)
    return [MenuOptionGroupRead.from_detail(g) for g in groups]


@entries_router.post(
    "/menu-option-groups",
    response_model=MenuOptionGroupRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createMenuOptionGroup",
)
async def create_menu_option_group(
    body: MenuOptionGroupCreateRequest, session: SessionDep, user: ManagerDep
) -> MenuOptionGroupRead:
    try:
        detail = await MenuService(session).create_option_group(
            user.store_id,
            name=body.name,
            min_select=body.min_select,
            max_select=body.max_select,
            options=[(o.name, o.price_delta) for o in body.options],
            sort_order=body.sort_order,
            actor_user_id=user.id,
        )
    except _MenuErrors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return MenuOptionGroupRead.from_detail(detail)


@entries_router.patch(
    "/menu-option-groups/{group_id}",
    response_model=MenuOptionGroupRead,
    operation_id="updateMenuOptionGroup",
)
async def update_menu_option_group(
    group_id: int, body: MenuOptionGroupUpdateRequest, session: SessionDep, user: ManagerDep
) -> MenuOptionGroupRead:
    try:
        detail = await MenuService(session).update_option_group(
            user.store_id,
            group_id,
            name=body.name,
            min_select=body.min_select,
            max_select=body.max_select,
            sort_order=body.sort_order,
        )
    except _MenuErrors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return MenuOptionGroupRead.from_detail(detail)


@entries_router.delete(
    "/menu-option-groups/{group_id}",
    response_model=MenuOptionGroupRead,
    operation_id="archiveMenuOptionGroup",
)
async def archive_menu_option_group(
    group_id: int, session: SessionDep, user: ManagerDep
) -> MenuOptionGroupRead:
    svc = MenuService(session)
    try:
        detail = await svc.get_option_group(user.store_id, group_id)
        await svc.archive_option_group(user.store_id, group_id)
    except _MenuErrors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return MenuOptionGroupRead.from_detail(detail)


# ── 選項 ──


@entries_router.post(
    "/menu-option-groups/{group_id}/options",
    response_model=MenuOptionRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="addMenuOption",
)
async def add_menu_option(
    group_id: int, body: MenuOptionInput, session: SessionDep, user: ManagerDep
) -> MenuOptionRead:
    try:
        option = await MenuService(session).add_option(
            user.store_id,
            group_id,
            name=body.name,
            price_delta=body.price_delta,
            actor_user_id=user.id,
        )
    except _MenuErrors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return MenuOptionRead.from_model(option)


@entries_router.patch(
    "/menu-options/{option_id}", response_model=MenuOptionRead, operation_id="updateMenuOption"
)
async def update_menu_option(
    option_id: int, body: MenuOptionUpdateRequest, session: SessionDep, user: ManagerDep
) -> MenuOptionRead:
    try:
        option = await MenuService(session).update_option(
            user.store_id,
            option_id,
            name=body.name,
            price_delta=body.price_delta,
            is_available=body.is_available,
            sort_order=body.sort_order,
            daily_limited=body.daily_limited,
            actor_user_id=user.id,
        )
    except _MenuErrors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return MenuOptionRead.from_model(option)


@entries_router.delete(
    "/menu-options/{option_id}", response_model=MenuOptionRead, operation_id="archiveMenuOption"
)
async def archive_menu_option(
    option_id: int, session: SessionDep, user: ManagerDep
) -> MenuOptionRead:
    try:
        option = await MenuService(session).archive_option(user.store_id, option_id)
    except _MenuErrors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return MenuOptionRead.from_model(option)


# ── 每日限量（docs/44 §3.7）：店員即可填／調整份數（開店檢查與營業中補貨） ──


@entries_router.get(
    "/menu-daily-stock",
    response_model=list[DailyStockEntryRead],
    operation_id="listMenuDailyStock",
)
async def list_menu_daily_stock(session: SessionDep, user: AuthDep) -> list[DailyStockEntryRead]:
    entries = await MenuService(session).list_daily_stock(user.store_id)
    return [DailyStockEntryRead.from_entry(e) for e in entries]


@entries_router.post(
    "/menu-daily-stock/{kind}/{target_id}/set",
    response_model=DailyStockEntryRead,
    operation_id="setMenuDailyStock",
)
async def set_menu_daily_stock(
    kind: MenuStockTarget,
    target_id: int,
    body: DailyStockSetRequest,
    session: SessionDep,
    user: AuthDep,
) -> DailyStockEntryRead:
    try:
        entry = await MenuService(session).set_daily_stock(
            user.store_id,
            kind,
            target_id,
            qty=body.qty,
            expected_remaining=body.expected_remaining,
            actor_user_id=user.id,
        )
    except _MenuErrors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return DailyStockEntryRead.from_entry(entry)


@entries_router.post(
    "/menu-daily-stock/{kind}/{target_id}/adjust",
    response_model=DailyStockEntryRead,
    operation_id="adjustMenuDailyStock",
)
async def adjust_menu_daily_stock(
    kind: MenuStockTarget,
    target_id: int,
    body: DailyStockAdjustRequest,
    session: SessionDep,
    user: AuthDep,
) -> DailyStockEntryRead:
    try:
        entry = await MenuService(session).adjust_daily_stock(
            user.store_id, kind, target_id, delta=body.delta, actor_user_id=user.id
        )
    except _MenuErrors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return DailyStockEntryRead.from_entry(entry)
