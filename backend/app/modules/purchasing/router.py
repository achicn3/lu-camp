"""purchasing 路由：供應商、採購單與補貨收貨。"""

from collections.abc import Awaitable
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.purchasing.models import InputInvoice
from app.modules.purchasing.schemas import (
    InputInvoiceDetailRead,
    InputInvoiceWrite,
    PurchaseOrderCreate,
    PurchaseOrderRead,
    PurchaseOrderUpdate,
    ReceiptAmountRead,
    ReceivePurchaseOrderRequest,
    ReceivePurchaseOrderResult,
    SupplierCreate,
    SupplierRead,
    SupplierUpdate,
)
from app.modules.purchasing.service import PurchasingService
from app.shared.enums import PurchaseOrderStatus, UserRole
from app.shared.exceptions import (
    CrossStoreReference,
    DomainError,
    DuplicateInputInvoice,
    IdempotencyKeyConflict,
    InputInvoiceAlreadySet,
    InputInvoiceInvalid,
    InputInvoiceNotFound,
    InsufficientStock,
    InvalidPurchaseOrder,
    PurchaseOrderNotCancellable,
    PurchaseOrderNotEditable,
    PurchaseOrderNotFound,
    PurchaseOrderNotReceivable,
    PurchaseOrderNotSubmittable,
    PurchasingManagerOnly,
    SupplierInactive,
    SupplierNotFound,
)
from app.shared.http import ERROR_CODE_HEADER
from app.shared.schemas import ListCountRead

router = APIRouter(tags=["purchasing"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]

_STATUS_BY_EXC: dict[type[DomainError], int] = {
    CrossStoreReference: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidPurchaseOrder: status.HTTP_422_UNPROCESSABLE_CONTENT,
    SupplierInactive: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PurchaseOrderNotFound: status.HTTP_404_NOT_FOUND,
    SupplierNotFound: status.HTTP_404_NOT_FOUND,
    PurchaseOrderNotReceivable: status.HTTP_409_CONFLICT,
    PurchaseOrderNotSubmittable: status.HTTP_409_CONFLICT,
    PurchaseOrderNotCancellable: status.HTTP_409_CONFLICT,
    InputInvoiceAlreadySet: status.HTTP_409_CONFLICT,
    InputInvoiceInvalid: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InputInvoiceNotFound: status.HTTP_404_NOT_FOUND,
    DuplicateInputInvoice: status.HTTP_409_CONFLICT,
    IdempotencyKeyConflict: status.HTTP_409_CONFLICT,
    PurchaseOrderNotEditable: status.HTTP_409_CONFLICT,
    InsufficientStock: status.HTTP_409_CONFLICT,
    PurchasingManagerOnly: status.HTTP_403_FORBIDDEN,
}

_ERROR_CODE_BY_EXC: dict[type[DomainError], str] = {
    IdempotencyKeyConflict: "IDEMPOTENCY_KEY_CONFLICT",
    PurchaseOrderNotReceivable: "PURCHASE_ORDER_NOT_RECEIVABLE",
    DuplicateInputInvoice: "DUPLICATE_INPUT_INVOICE",
}

# 同店同號同日的進項發票只能一張（併發登錄撞到時轉 409）。
_INPUT_INVOICE_UNIQUE = "uq_purchase_input_invoices_store_number_date"


def _map_domain_error(exc: DomainError) -> HTTPException:
    error_code = _ERROR_CODE_BY_EXC.get(type(exc))
    return HTTPException(
        status_code=_STATUS_BY_EXC.get(type(exc), status.HTTP_400_BAD_REQUEST),
        detail=str(exc),
        headers={ERROR_CODE_HEADER: error_code} if error_code is not None else None,
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
    include_inactive: Annotated[bool, Query()] = False,
) -> list[SupplierRead]:
    """預設只列啟用中供應商（建單選單用）；include_inactive=true 列全部（供應商管理用）。"""
    suppliers = await PurchasingService(session).list_suppliers(
        user.store_id, q=q, limit=limit, offset=offset, include_inactive=include_inactive
    )
    return [SupplierRead.model_validate(supplier) for supplier in suppliers]


@router.get("/suppliers/count", response_model=ListCountRead, operation_id="countSuppliers")
async def count_suppliers(
    session: SessionDep,
    user: CurrentUserDep,
    q: Annotated[str | None, Query(max_length=100)] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> ListCountRead:
    """符合同一組篩選的供應商總筆數；供應商管理頁用它顯示「第 X / Y 頁」。"""
    total = await PurchasingService(session).count_suppliers(
        user.store_id, q=q, include_inactive=include_inactive
    )
    return ListCountRead(count=total)


@router.get("/suppliers/{supplier_id}", response_model=SupplierRead, operation_id="getSupplier")
async def get_supplier(supplier_id: int, session: SessionDep, user: CurrentUserDep) -> SupplierRead:
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


@router.put(
    "/purchase-orders/{purchase_order_id}",
    response_model=PurchaseOrderRead,
    operation_id="updatePurchaseOrder",
)
async def update_purchase_order(
    purchase_order_id: int, payload: PurchaseOrderUpdate, session: SessionDep, user: CurrentUserDep
) -> PurchaseOrderRead:
    """修改採購單（整張覆寫）：草稿全員可改，已下單／已收貨限管理者；已收差額自動調庫存。"""
    svc = PurchasingService(session)
    try:
        purchase_order = await svc.update_purchase_order(
            user.store_id,
            purchase_order_id,
            payload,
            actor_user_id=user.id,
            actor_is_manager=user.role == UserRole.MANAGER,
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
    "/purchase-orders/count", response_model=ListCountRead, operation_id="countPurchaseOrders"
)
async def count_purchase_orders(
    session: SessionDep,
    user: CurrentUserDep,
    po_status: Annotated[list[PurchaseOrderStatus] | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> ListCountRead:
    """符合同一組篩選的採購單總筆數；採購頁用它顯示「第 X / Y 頁」。"""
    total = await PurchasingService(session).count_purchase_orders(
        user.store_id, statuses=po_status, q=q
    )
    return ListCountRead(count=total)


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
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=80)],
) -> ReceivePurchaseOrderResult:
    """分批收貨：各明細本次實收量＋選填進項發票；全收足轉已收貨，否則部分到貨。

    帶 Idempotency-Key：同 key 重送只入庫一次、回原結果（防網路重試重複入庫，docs/19）。
    """
    svc = PurchasingService(session)
    try:
        purchase_order, receipt = await svc.receive_purchase_order(
            user.store_id,
            purchase_order_id,
            actor_user_id=user.id,
            lines=payload.lines,
            idempotency_key=idempotency_key,
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
        if _INPUT_INVOICE_UNIQUE in str(exc.orig):
            raise _duplicate_invoice() from exc
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


# ── 進項發票（docs/70 §5）──────────────────────────────────────────


def _duplicate_invoice() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="這張發票（同號同日）已登錄過，不可重複入帳",
        headers={ERROR_CODE_HEADER: "DUPLICATE_INPUT_INVOICE"},
    )


async def _invoice_detail(
    svc: PurchasingService, store_id: int, invoice: InputInvoice
) -> InputInvoiceDetailRead:
    amounts = await svc.receipt_amounts(store_id, invoice.receipts)
    receipts = [
        ReceiptAmountRead(
            receipt_id=receipt.id,
            purchase_order_id=receipt.purchase_order_id,
            received_at=receipt.received_at,
            amount=amounts[receipt.id],
        )
        for receipt in invoice.receipts
    ]
    return InputInvoiceDetailRead(
        id=invoice.id,
        supplier_id=invoice.supplier_id,
        supplier_name=invoice.supplier_name,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        invoice_total=invoice.invoice_total,
        invoice_net=invoice.invoice_net,
        invoice_tax=invoice.invoice_tax,
        created_at=invoice.created_at,
        receipts=receipts,
        receipts_total=sum((r.amount for r in receipts), Decimal(0)),
    )


async def _commit_invoice(
    session: AsyncSession, svc: PurchasingService, store_id: int, writing: Awaitable[InputInvoice]
) -> InputInvoiceDetailRead:
    """建立／修改發票共用：領域錯誤照表轉、同號同日併發撞唯一鍵轉 409。"""
    try:
        invoice = await writing
        detail = await _invoice_detail(svc, store_id, invoice)
    except DomainError as exc:
        await session.rollback()
        raise _map_domain_error(exc) from exc
    except IntegrityError as exc:
        await session.rollback()
        if _INPUT_INVOICE_UNIQUE in str(exc.orig):
            raise _duplicate_invoice() from exc
        raise
    await session.commit()
    return detail


@router.get(
    "/purchase-input-invoices",
    response_model=list[InputInvoiceDetailRead],
    operation_id="listInputInvoices",
)
async def list_input_invoices(
    session: SessionDep,
    user: CurrentUserDep,
    supplier_id: Annotated[int | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[InputInvoiceDetailRead]:
    """進項發票清單（發票日期新到舊），可依供應商篩選。"""
    svc = PurchasingService(session)
    invoices = await svc.list_input_invoices(
        user.store_id, supplier_id=supplier_id, limit=limit, offset=offset
    )
    return [await _invoice_detail(svc, user.store_id, invoice) for invoice in invoices]


@router.get(
    "/purchase-input-invoices/count",
    response_model=ListCountRead,
    operation_id="countInputInvoices",
)
async def count_input_invoices(
    session: SessionDep,
    user: CurrentUserDep,
    supplier_id: Annotated[int | None, Query()] = None,
) -> ListCountRead:
    total = await PurchasingService(session).count_input_invoices(
        user.store_id, supplier_id=supplier_id
    )
    return ListCountRead(count=total)


@router.get(
    "/purchase-input-invoices/{invoice_id}",
    response_model=InputInvoiceDetailRead,
    operation_id="getInputInvoice",
)
async def get_input_invoice(
    invoice_id: int, session: SessionDep, user: CurrentUserDep
) -> InputInvoiceDetailRead:
    svc = PurchasingService(session)
    invoice = await svc.get_input_invoice(user.store_id, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到進項發票")
    return await _invoice_detail(svc, user.store_id, invoice)


@router.post(
    "/purchase-input-invoices",
    response_model=InputInvoiceDetailRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createInputInvoice",
)
async def create_input_invoice(
    payload: InputInvoiceWrite, session: SessionDep, user: CurrentUserDep
) -> InputInvoiceDetailRead:
    """登錄進項發票並掛上它涵蓋的收貨（可多批、跨採購單，須同一供應商）。"""
    svc = PurchasingService(session)
    return await _commit_invoice(
        session,
        svc,
        user.store_id,
        svc.create_input_invoice(user.store_id, payload, actor_user_id=user.id),
    )


@router.put(
    "/purchase-input-invoices/{invoice_id}",
    response_model=InputInvoiceDetailRead,
    operation_id="updateInputInvoice",
)
async def update_input_invoice(
    invoice_id: int, payload: InputInvoiceWrite, session: SessionDep, user: CurrentUserDep
) -> InputInvoiceDetailRead:
    """更正進項發票（號碼、日期、金額、涵蓋的收貨）；限管理者。"""
    svc = PurchasingService(session)
    return await _commit_invoice(
        session,
        svc,
        user.store_id,
        svc.update_input_invoice(
            user.store_id,
            invoice_id,
            payload,
            actor_user_id=user.id,
            actor_is_manager=user.role == UserRole.MANAGER,
        ),
    )


@router.delete(
    "/purchase-input-invoices/{invoice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteInputInvoice",
)
async def delete_input_invoice(invoice_id: int, session: SessionDep, user: CurrentUserDep) -> None:
    """刪掉登錯的發票（涵蓋的收貨回到還沒開發票）；限管理者。"""
    try:
        await PurchasingService(session).delete_input_invoice(
            user.store_id,
            invoice_id,
            actor_user_id=user.id,
            actor_is_manager=user.role == UserRole.MANAGER,
        )
    except DomainError as exc:
        await session.rollback()
        raise _map_domain_error(exc) from exc
    await session.commit()


@router.get(
    "/suppliers/{supplier_id}/uninvoiced-receipts",
    response_model=list[ReceiptAmountRead],
    operation_id="listUninvoicedReceipts",
)
async def list_uninvoiced_receipts(
    supplier_id: int, session: SessionDep, user: CurrentUserDep
) -> list[ReceiptAmountRead]:
    """某供應商還沒開發票的收貨批次與金額（登錄發票時勾選）。"""
    svc = PurchasingService(session)
    receipts = await svc.uninvoiced_receipts(user.store_id, supplier_id)
    amounts = await svc.receipt_amounts(user.store_id, receipts)
    return [
        ReceiptAmountRead(
            receipt_id=receipt.id,
            purchase_order_id=receipt.purchase_order_id,
            received_at=receipt.received_at,
            amount=amounts[receipt.id],
        )
        for receipt in receipts
    ]
