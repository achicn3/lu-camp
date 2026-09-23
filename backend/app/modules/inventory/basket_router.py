"""散裝販售籃 API（ADR-025）。只做 I/O 與驗證；規則在 BulkBasketService。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.inventory.basket_service import BasketView, BulkBasketService, unit_cost
from app.modules.inventory.schemas import (
    BulkBasketAddLot,
    BulkBasketCreate,
    BulkBasketRead,
    BulkBasketSource,
    BulkBasketUpdate,
    BulkCostReference,
)
from app.shared.exceptions import BulkBasketConflict, BulkBasketNotFound, CrossStoreReference

router = APIRouter(tags=["inventory"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role("MANAGER"))]

_NOT_FOUND = "找不到此販售籃"


def _read(view: BasketView) -> BulkBasketRead:
    basket = view.basket
    return BulkBasketRead(
        id=basket.id,
        store_id=basket.store_id,
        code=basket.code,
        name=basket.name,
        brand_id=basket.brand_id,
        category_id=basket.category_id,
        unit_price=basket.unit_price,
        note=basket.note,
        is_active=basket.is_active,
        remaining_qty=view.remaining_qty,
        sources=[
            BulkBasketSource(
                bulk_lot_id=lot.id,
                lot_code=lot.lot_code,
                intake_date=lot.intake_date,
                total_qty=lot.total_qty,
                remaining_qty=lot.remaining_qty,
                status=lot.status,
                acquisition_cost=lot.acquisition_cost,
                unit_cost=unit_cost(lot),
                note=lot.note,
            )
            for lot in view.sources
        ],
        cost_reference=BulkCostReference(**view.cost_reference),
    )


async def _fail(session: AsyncSession, exc: Exception) -> HTTPException:
    await session.rollback()
    if isinstance(exc, BulkBasketNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, BulkBasketConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


@router.get("/bulk-baskets", response_model=list[BulkBasketRead], operation_id="listBulkBaskets")
async def list_bulk_baskets(
    session: SessionDep,
    user: CurrentUserDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    include_inactive: bool = False,
) -> list[BulkBasketRead]:
    """販售籃清單（收購選籃、庫存管理）。預設只列啟用中的。"""
    views = await BulkBasketService(session).list_baskets(
        user.store_id, q=q, include_inactive=include_inactive
    )
    return [_read(v) for v in views]


@router.post(
    "/bulk-baskets",
    response_model=BulkBasketRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createBulkBasket",
)
async def create_bulk_basket(
    payload: BulkBasketCreate, session: SessionDep, user: CurrentUserDep
) -> BulkBasketRead:
    """建立空的販售籃（店員收購時也要能開新籃，故不限管理者）。"""
    try:
        view = await BulkBasketService(session).create(
            user.store_id,
            name=payload.name,
            unit_price=payload.unit_price,
            brand_id=payload.brand_id,
            category_id=payload.category_id,
            note=payload.note,
            actor_user_id=user.id,
        )
    except CrossStoreReference as exc:
        raise await _fail(session, exc) from exc
    await session.commit()
    return _read(view)


@router.get(
    "/bulk-baskets/by-code/{code}",
    response_model=BulkBasketRead,
    operation_id="getBulkBasketByCode",
)
async def get_bulk_basket_by_code(
    code: str, session: SessionDep, user: CurrentUserDep
) -> BulkBasketRead:
    """POS 掃籃子標籤（條碼即 Code 128 編 code）。"""
    view = await BulkBasketService(session).get_by_code(user.store_id, code)
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return _read(view)


@router.get(
    "/bulk-baskets/{basket_id}", response_model=BulkBasketRead, operation_id="getBulkBasket"
)
async def get_bulk_basket(
    basket_id: int, session: SessionDep, user: CurrentUserDep
) -> BulkBasketRead:
    view = await BulkBasketService(session).get(user.store_id, basket_id)
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return _read(view)


@router.patch(
    "/bulk-baskets/{basket_id}", response_model=BulkBasketRead, operation_id="updateBulkBasket"
)
async def update_bulk_basket(
    basket_id: int, payload: BulkBasketUpdate, session: SessionDep, user: ManagerDep
) -> BulkBasketRead:
    """改販售籃（限管理者）。改售價後記得重印標籤；已成交的價格與成本不受影響。"""
    try:
        view = await BulkBasketService(session).update(
            user.store_id,
            basket_id,
            # 不用 model_dump：NTDAmount 的序列化器會把 Decimal 轉成字串。
            {key: getattr(payload, key) for key in payload.model_fields_set},
            actor_user_id=user.id,
        )
    except (CrossStoreReference, BulkBasketConflict) as exc:
        raise await _fail(session, exc) from exc
    if view is None:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    await session.commit()
    return _read(view)


@router.post(
    "/bulk-baskets/{basket_id}/lots",
    response_model=BulkBasketRead,
    operation_id="addLotToBulkBasket",
)
async def add_lot_to_bulk_basket(
    basket_id: int, payload: BulkBasketAddLot, session: SessionDep, user: ManagerDep
) -> BulkBasketRead:
    """既有散裝整批加入販售籃（限管理者；同價、自有、未入其他籃）。"""
    try:
        view = await BulkBasketService(session).add_existing_lot(
            user.store_id, basket_id, payload.bulk_lot_id, actor_user_id=user.id
        )
    except (BulkBasketNotFound, BulkBasketConflict) as exc:
        raise await _fail(session, exc) from exc
    await session.commit()
    return _read(view)
