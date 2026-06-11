"""inventory 唯讀查詢路由（T19-pre-B）：掃碼查件、序號品/數量品/散裝堆列表。

只做 I/O 與驗證（§2）；全部需認證、以 token 的 store_id 範圍過濾（§4）。
寫入（建檔/改價/狀態轉移）不在此 router——由 acquisition/sales 等流程經
service 進行，庫存頁的改價/調整功能屬後續任務。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.inventory.schemas import BulkLotRead, CatalogProductRead, SerializedItemRead
from app.modules.inventory.service import InventoryService
from app.shared.enums import BulkLotStatus, OwnershipType, SerializedItemStatus

router = APIRouter(tags=["inventory"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


@router.get(
    "/serialized-items/by-code/{item_code}",
    response_model=SerializedItemRead,
    operation_id="getSerializedItemByCode",
)
async def get_serialized_by_code(
    item_code: str, session: SessionDep, user: CurrentUserDep
) -> SerializedItemRead:
    """POS 掃碼查件：以 item_code 取序號品（他店/不存在一律 404，不洩漏跨店資料）。"""
    item = await InventoryService(session).get_serialized_by_code(user.store_id, item_code)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此識別碼的序號品")
    return SerializedItemRead.model_validate(item)


@router.get(
    "/serialized-items",
    response_model=list[SerializedItemRead],
    operation_id="listSerializedItems",
)
async def list_serialized(
    session: SessionDep,
    user: CurrentUserDep,
    status_filter: Annotated[SerializedItemStatus | None, Query(alias="status")] = None,
    ownership_type: Annotated[OwnershipType | None, Query(alias="ownership")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SerializedItemRead]:
    items = await InventoryService(session).list_serialized(
        user.store_id,
        status=status_filter,
        ownership_type=ownership_type,
        limit=limit,
        offset=offset,
    )
    return [SerializedItemRead.model_validate(item) for item in items]


@router.get(
    "/catalog-products",
    response_model=list[CatalogProductRead],
    operation_id="listCatalogProducts",
)
async def list_catalog(
    session: SessionDep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CatalogProductRead]:
    products = await InventoryService(session).list_catalog(
        user.store_id, limit=limit, offset=offset
    )
    return [CatalogProductRead.model_validate(product) for product in products]


@router.get(
    "/bulk-lots/by-code/{lot_code}",
    response_model=BulkLotRead,
    operation_id="getBulkLotByCode",
)
async def get_bulk_lot_by_code(
    lot_code: str, session: SessionDep, user: CurrentUserDep
) -> BulkLotRead:
    """POS 掃堆標籤：以 lot_code 取散裝堆（docs/04；標籤條碼即 Code 128 編 lot_code）。"""
    lot = await InventoryService(session).get_bulk_lot_by_code(user.store_id, lot_code)
    if lot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到此識別碼的散裝堆")
    return BulkLotRead.model_validate(lot)


@router.get("/bulk-lots", response_model=list[BulkLotRead], operation_id="listBulkLots")
async def list_bulk_lots(
    session: SessionDep,
    user: CurrentUserDep,
    status_filter: Annotated[BulkLotStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BulkLotRead]:
    lots = await InventoryService(session).list_bulk_lots(
        user.store_id, status=status_filter, limit=limit, offset=offset
    )
    return [BulkLotRead.model_validate(lot) for lot in lots]
