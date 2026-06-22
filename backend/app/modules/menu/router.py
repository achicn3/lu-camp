"""menu 路由：餐飲菜單品項 CRUD（docs/10）。

讀取（POS 取菜單磚）開放給任何登入者；新增/改價/上下架/封存限 MANAGER（§管理權限）。
只做 I/O 與驗證；業務邏輯在 service。領域例外對應 HTTP：NotFound→404、Duplicate→409、
售價不合法→422。寫入端點成功才 commit（get_session 不自動 commit）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.menu.schemas import (
    MenuItemCreateRequest,
    MenuItemRead,
    MenuItemUpdateRequest,
)
from app.modules.menu.service import MenuService
from app.shared.exceptions import DuplicateMenuItem, MenuItemNotFound, SaleLineInvalid

router = APIRouter(prefix="/menu-items", tags=["menu"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role("MANAGER"))]


@router.get("", response_model=list[MenuItemRead], operation_id="listMenuItems")
async def list_menu_items(
    session: SessionDep,
    user: AuthDep,
    available_only: Annotated[bool, Query()] = False,
) -> list[MenuItemRead]:
    items = await MenuService(session).list_items(
        user.store_id, include_unavailable=not available_only
    )
    return [MenuItemRead.from_model(i) for i in items]


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
            category=body.category,
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
    return MenuItemRead.from_model(item)


@router.patch("/{item_id}", response_model=MenuItemRead, operation_id="updateMenuItem")
async def update_menu_item(
    item_id: int, body: MenuItemUpdateRequest, session: SessionDep, user: ManagerDep
) -> MenuItemRead:
    # category 以 model_fields_set 區分「未提供（不變）」與「明確 null（清空）」：
    # 只在有提供時才傳 category，否則交給 service 預設 sentinel（不變）。
    category_kw: dict[str, str | None] = (
        {"category": body.category} if "category" in body.model_fields_set else {}
    )
    try:
        item = await MenuService(session).update_menu_item(
            user.store_id,
            item_id,
            name=body.name,
            unit_price=body.unit_price,
            sort_order=body.sort_order,
            is_available=body.is_available,
            actor_user_id=user.id,
            **category_kw,
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
    return MenuItemRead.from_model(item)


@router.delete("/{item_id}", response_model=MenuItemRead, operation_id="archiveMenuItem")
async def archive_menu_item(
    item_id: int, session: SessionDep, user: ManagerDep
) -> MenuItemRead:
    try:
        item = await MenuService(session).archive_menu_item(
            user.store_id, item_id, actor_user_id=user.id
        )
    except MenuItemNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await session.commit()
    return MenuItemRead.from_model(item)
