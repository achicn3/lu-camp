"""purchasing 路由：供應商、採購單與補貨收貨。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.purchasing.schemas import (
    InputInvoiceIn,
    InputInvoiceRead,
    PurchaseOrderCreate,
    PurchaseOrderRead,
    ReceivePurchaseOrderRequest,
    ReceivePurchaseOrderResult,
    SupplierCreate,
    SupplierRead,
)
from app.modules.purchasing.service import PurchasingService
from app.shared.enums import PurchaseOrderStatus
from app.shared.exceptions import (
    CrossStoreReference,
    DomainError,
    InputInvoiceAlreadySet,
    InvalidPurchaseOrder,
    PurchaseOrderNotFound,
    PurchaseOrderNotReceivable,
    PurchaseOrderNotReceived,
)

router = APIRouter(tags=["purchasing"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]

_STATUS_BY_EXC: dict[type[DomainError], int] = {
    CrossStoreReference: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidPurchaseOrder: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PurchaseOrderNotFound: status.HTTP_404_NOT_FOUND,
    PurchaseOrderNotReceivable: status.HTTP_409_CONFLICT,
    InputInvoiceAlreadySet: status.HTTP_409_CONFLICT,
    PurchaseOrderNotReceived: status.HTTP_409_CONFLICT,
}


def _map_domain_error(exc: DomainError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_BY_EXC.get(type(exc), status.HTTP_400_BAD_REQUEST),
        detail=str(exc),
    )


@router.post(
    "/suppliers",
    response_model=SupplierRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSupplier",
)
async def create_supplier(
    payload: SupplierCreate, session: SessionDep, user: CurrentUserDep
) -> SupplierRead:
    svc = PurchasingService(session)
    try:
        supplier = await svc.create_supplier(user.store_id, payload)
    except DomainError as exc:
        await session.rollback()
        raise _map_domain_error(exc) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="供應商名稱重複") from exc
    await session.commit()
    return SupplierRead.model_validate(supplier)


@router.get("/suppliers", response_model=list[SupplierRead], operation_id="listSuppliers")
async def list_suppliers(
    session: SessionDep,
    user: CurrentUserDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SupplierRead]:
    suppliers = await PurchasingService(session).list_suppliers(
        user.store_id, q=q, limit=limit, offset=offset
    )
    return [SupplierRead.model_validate(supplier) for supplier in suppliers]


@router.post(
    "/purchase-orders",
    response_model=PurchaseOrderRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createPurchaseOrder",
)
async def create_purchase_order(
    payload: PurchaseOrderCreate, session: SessionDep, user: CurrentUserDep
) -> PurchaseOrderRead:
    svc = PurchasingService(session)
    try:
        purchase_order = await svc.create_purchase_order(
            user.store_id, payload, actor_user_id=user.id
        )
    except DomainError as exc:
        await session.rollback()
        raise _map_domain_error(exc) from exc
    await session.commit()
    return PurchaseOrderRead.from_model(purchase_order)


@router.get(
    "/purchase-orders",
    response_model=list[PurchaseOrderRead],
    operation_id="listPurchaseOrders",
)
async def list_purchase_orders(
    session: SessionDep,
    user: CurrentUserDep,
    po_status: Annotated[PurchaseOrderStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PurchaseOrderRead]:
    purchase_orders = await PurchasingService(session).list_purchase_orders(
        user.store_id, status=po_status, limit=limit, offset=offset
    )
    return [PurchaseOrderRead.from_model(po) for po in purchase_orders]


@router.get(
    "/purchase-orders/{purchase_order_id}",
    response_model=PurchaseOrderRead,
    operation_id="getPurchaseOrder",
)
async def get_purchase_order(
    purchase_order_id: int, session: SessionDep, user: CurrentUserDep
) -> PurchaseOrderRead:
    purchase_order = await PurchasingService(session).get_purchase_order(
        user.store_id, purchase_order_id
    )
    if purchase_order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到採購單")
    return PurchaseOrderRead.from_model(purchase_order)


@router.post(
    "/purchase-orders/{purchase_order_id}/receive",
    response_model=ReceivePurchaseOrderResult,
    operation_id="receivePurchaseOrder",
)
async def receive_purchase_order(
    purchase_order_id: int,
    session: SessionDep,
    user: CurrentUserDep,
    payload: ReceivePurchaseOrderRequest | None = None,
) -> ReceivePurchaseOrderResult:
    svc = PurchasingService(session)
    try:
        purchase_order, receipt = await svc.receive_purchase_order(
            user.store_id,
            purchase_order_id,
            actor_user_id=user.id,
            invoice=payload.invoice if payload is not None else None,
        )
    except DomainError as exc:
        await session.rollback()
        raise _map_domain_error(exc) from exc
    except IntegrityError as exc:
        await session.rollback()
        if "uq_goods_receipts_store_invoice" in str(exc.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="此發票號碼（同日期）已登錄於其他採購單，不可重複入帳",
            ) from exc
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="採購單已收貨") from exc
    await session.commit()
    return ReceivePurchaseOrderResult(
        receipt_id=receipt.id,
        purchase_order=PurchaseOrderRead.from_model(purchase_order),
    )


@router.post(
    "/purchase-orders/{purchase_order_id}/invoice",
    response_model=InputInvoiceRead,
    operation_id="registerInputInvoice",
)
async def register_input_invoice(
    purchase_order_id: int,
    payload: InputInvoiceIn,
    session: SessionDep,
    user: CurrentUserDep,
) -> InputInvoiceRead:
    """補登進項發票（收貨時漏登；已登錄不可覆寫 → 409）。"""
    svc = PurchasingService(session)
    try:
        receipt = await svc.register_input_invoice(
            user.store_id, purchase_order_id, invoice=payload
        )
    except DomainError as exc:
        await session.rollback()
        raise _map_domain_error(exc) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="此發票號碼（同日期）已登錄於其他採購單，不可重複入帳",
        ) from exc
    await session.commit()
    return InputInvoiceRead.model_validate(receipt)
