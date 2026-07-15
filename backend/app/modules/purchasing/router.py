"""purchasing 路由：供應商、採購單與補貨收貨。"""

import hashlib
import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
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
    SupplierUpdate,
)
from app.modules.purchasing.service import PurchasingService
from app.shared.enums import PurchaseOrderStatus
from app.shared.exceptions import (
    CrossStoreReference,
    DomainError,
    IdempotencyKeyConflict,
    InputInvoiceAlreadySet,
    InvalidPurchaseOrder,
    PurchaseOrderNotCancellable,
    PurchaseOrderNotFound,
    PurchaseOrderNotReceivable,
    PurchaseOrderNotReceived,
    PurchaseOrderNotSubmittable,
    SupplierNotFound,
)
from app.shared.http import ERROR_CODE_HEADER

router = APIRouter(tags=["purchasing"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]

_STATUS_BY_EXC: dict[type[DomainError], int] = {
    CrossStoreReference: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidPurchaseOrder: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PurchaseOrderNotFound: status.HTTP_404_NOT_FOUND,
    SupplierNotFound: status.HTTP_404_NOT_FOUND,
    PurchaseOrderNotReceivable: status.HTTP_409_CONFLICT,
    PurchaseOrderNotSubmittable: status.HTTP_409_CONFLICT,
    PurchaseOrderNotCancellable: status.HTTP_409_CONFLICT,
    InputInvoiceAlreadySet: status.HTTP_409_CONFLICT,
    PurchaseOrderNotReceived: status.HTTP_409_CONFLICT,
    IdempotencyKeyConflict: status.HTTP_409_CONFLICT,
}

_ERROR_CODE_BY_EXC: dict[type[DomainError], str] = {
    IdempotencyKeyConflict: "IDEMPOTENCY_KEY_CONFLICT",
    PurchaseOrderNotReceivable: "PURCHASE_ORDER_NOT_RECEIVABLE",
}


def _map_domain_error(exc: DomainError) -> HTTPException:
    error_code = _ERROR_CODE_BY_EXC.get(type(exc))
    return HTTPException(
        status_code=_STATUS_BY_EXC.get(type(exc), status.HTTP_400_BAD_REQUEST),
        detail=str(exc),
        headers={ERROR_CODE_HEADER: error_code} if error_code is not None else None,
    )


def _receive_fingerprint(purchase_order_id: int, payload: ReceivePurchaseOrderRequest) -> str:
    """收貨請求指紋：同 Idempotency-Key 重送時用以辨識「同一請求（回放）」vs「不同請求（409）」。"""
    canonical = {
        "purchase_order_id": purchase_order_id,
        "lines": sorted((line.line_id, line.qty) for line in payload.lines),
        "invoice": (
            None
            if payload.invoice is None
            else [
                payload.invoice.invoice_number,
                payload.invoice.invoice_date.isoformat(),
                str(payload.invoice.invoice_total),
            ]
        ),
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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
    include_inactive: Annotated[bool, Query()] = False,
) -> list[SupplierRead]:
    """預設只列啟用中供應商（建單選單用）；include_inactive=true 列全部（供應商管理用）。"""
    suppliers = await PurchasingService(session).list_suppliers(
        user.store_id, q=q, limit=limit, offset=offset, include_inactive=include_inactive
    )
    return [SupplierRead.model_validate(supplier) for supplier in suppliers]


@router.get(
    "/suppliers/{supplier_id}", response_model=SupplierRead, operation_id="getSupplier"
)
async def get_supplier(
    supplier_id: int, session: SessionDep, user: CurrentUserDep
) -> SupplierRead:
    svc = PurchasingService(session)
    try:
        supplier = await svc.get_supplier(user.store_id, supplier_id)
    except DomainError as exc:
        raise _map_domain_error(exc) from exc
    return SupplierRead.model_validate(supplier)


@router.patch(
    "/suppliers/{supplier_id}", response_model=SupplierRead, operation_id="updateSupplier"
)
async def update_supplier(
    supplier_id: int, payload: SupplierUpdate, session: SessionDep, user: CurrentUserDep
) -> SupplierRead:
    """編輯供應商名稱/聯絡方式/統編。"""
    svc = PurchasingService(session)
    try:
        supplier = await svc.update_supplier(
            user.store_id, supplier_id, payload, actor_user_id=user.id
        )
    except DomainError as exc:
        await session.rollback()
        raise _map_domain_error(exc) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="供應商名稱重複") from exc
    await session.commit()
    return SupplierRead.model_validate(supplier)


@router.post(
    "/suppliers/{supplier_id}/deactivate",
    response_model=SupplierRead,
    operation_id="deactivateSupplier",
)
async def deactivate_supplier(
    supplier_id: int, session: SessionDep, user: CurrentUserDep
) -> SupplierRead:
    """停用供應商（不進建單選單，保留歷史）。"""
    svc = PurchasingService(session)
    try:
        supplier = await svc.set_supplier_active(
            user.store_id, supplier_id, False, actor_user_id=user.id
        )
    except DomainError as exc:
        await session.rollback()
        raise _map_domain_error(exc) from exc
    await session.commit()
    return SupplierRead.model_validate(supplier)


@router.post(
    "/suppliers/{supplier_id}/activate",
    response_model=SupplierRead,
    operation_id="activateSupplier",
)
async def activate_supplier(
    supplier_id: int, session: SessionDep, user: CurrentUserDep
) -> SupplierRead:
    """重新啟用供應商。"""
    svc = PurchasingService(session)
    try:
        supplier = await svc.set_supplier_active(
            user.store_id, supplier_id, True, actor_user_id=user.id
        )
    except DomainError as exc:
        await session.rollback()
        raise _map_domain_error(exc) from exc
    await session.commit()
    return SupplierRead.model_validate(supplier)


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


@router.post(
    "/purchase-orders/{purchase_order_id}/submit",
    response_model=PurchaseOrderRead,
    operation_id="submitPurchaseOrder",
)
async def submit_purchase_order(
    purchase_order_id: int, session: SessionDep, user: CurrentUserDep
) -> PurchaseOrderRead:
    """草稿送出 → 已下單（計入待到貨、可收貨）。"""
    svc = PurchasingService(session)
    try:
        purchase_order = await svc.submit_purchase_order(
            user.store_id, purchase_order_id, actor_user_id=user.id
        )
    except DomainError as exc:
        await session.rollback()
        raise _map_domain_error(exc) from exc
    await session.commit()
    return PurchaseOrderRead.from_model(purchase_order)


@router.post(
    "/purchase-orders/{purchase_order_id}/cancel",
    response_model=PurchaseOrderRead,
    operation_id="cancelPurchaseOrder",
)
async def cancel_purchase_order(
    purchase_order_id: int, session: SessionDep, user: CurrentUserDep
) -> PurchaseOrderRead:
    """取消採購單 → 已取消（僅草稿/已下單且尚未收貨可取消）。"""
    svc = PurchasingService(session)
    try:
        purchase_order = await svc.cancel_purchase_order(
            user.store_id, purchase_order_id, actor_user_id=user.id
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
    po_status: Annotated[list[PurchaseOrderStatus] | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PurchaseOrderRead]:
    """狀態篩選可帶多值（?status=ORDERED&status=PARTIAL）；「待收貨」＝ORDERED＋PARTIAL。
    q 以單號（純數字）或供應商名搜尋。"""
    purchase_orders = await PurchasingService(session).list_purchase_orders(
        user.store_id, statuses=po_status, q=q, limit=limit, offset=offset
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
    responses={
        409: {
            "description": "收貨衝突；回應標頭提供穩定錯誤代碼以區分冪等與已回滾的業務衝突。",
            "headers": {
                ERROR_CODE_HEADER: {
                    "description": "IDEMPOTENCY_KEY_CONFLICT、DUPLICATE_INPUT_INVOICE 等穩定代碼",
                    "schema": {"type": "string"},
                }
            },
        }
    },
)
async def receive_purchase_order(
    purchase_order_id: int,
    payload: ReceivePurchaseOrderRequest,
    session: SessionDep,
    user: CurrentUserDep,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=80)
    ],
) -> ReceivePurchaseOrderResult:
    """分批收貨：各明細本次實收量＋選填進項發票；全收足轉已收貨，否則部分到貨。

    帶 Idempotency-Key：同 key 重送只入庫一次、回原結果（防網路重試重複入庫，docs/19）。
    """
    fingerprint = _receive_fingerprint(purchase_order_id, payload)
    svc = PurchasingService(session)
    try:
        purchase_order, receipt = await svc.receive_purchase_order(
            user.store_id,
            purchase_order_id,
            actor_user_id=user.id,
            lines=payload.lines,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            invoice=payload.invoice,
        )
    except DomainError as exc:
        await session.rollback()
        raise _map_domain_error(exc) from exc
    except IntegrityError as exc:
        await session.rollback()
        # 並行首寫競態：同 key 兩請求同時插入，唯一索引擋下輸家 → 回放贏家的結果／或指紋不符 409。
        if "uq_goods_receipts_store_idempotency" in str(exc.orig):
            try:
                purchase_order, receipt = await svc.receive_purchase_order(
                    user.store_id,
                    purchase_order_id,
                    actor_user_id=user.id,
                    lines=payload.lines,
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    invoice=payload.invoice,
                )
            except DomainError as replay_exc:
                await session.rollback()
                raise _map_domain_error(replay_exc) from replay_exc
            await session.commit()
            return ReceivePurchaseOrderResult(
                receipt_id=receipt.id,
                purchase_order=PurchaseOrderRead.from_model(purchase_order),
            )
        if "uq_goods_receipts_store_invoice" in str(exc.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="此發票號碼（同日期）已登錄於其他採購單，不可重複入帳",
                headers={ERROR_CODE_HEADER: "DUPLICATE_INPUT_INVOICE"},
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="收貨失敗",
            headers={ERROR_CODE_HEADER: "RECEIVE_CONFLICT"},
        ) from exc
    await session.commit()
    return ReceivePurchaseOrderResult(
        receipt_id=receipt.id,
        purchase_order=PurchaseOrderRead.from_model(purchase_order),
    )


@router.post(
    "/purchase-orders/{purchase_order_id}/receipts/{receipt_id}/invoice",
    response_model=InputInvoiceRead,
    operation_id="registerInputInvoice",
)
async def register_input_invoice(
    purchase_order_id: int,
    receipt_id: int,
    payload: InputInvoiceIn,
    session: SessionDep,
    user: CurrentUserDep,
) -> InputInvoiceRead:
    """補登某收貨批次的進項發票（收貨時漏登；已登錄不可覆寫 → 409）。"""
    svc = PurchasingService(session)
    try:
        receipt = await svc.register_input_invoice(
            user.store_id, purchase_order_id, receipt_id, invoice=payload
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
