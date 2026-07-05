"""einvoice 路由（T14 殼）：發票查詢、上傳佇列檢視/重送、回執記錄。

發票的建立/開立由未來的銷售結帳流程（einvoice_enabled=true 時）於原子交易內經 service
進行；本 router 只暴露查詢、MANAGER 可見的佇列與重送、回執記錄。實際拋檔/Turnkey 上傳
由收尾階段（憑證/主機到位）搭配 XSD-backed 序列化器與排程接手。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.einvoice.schemas import (
    EInvoiceQueueItemRead,
    EInvoiceQueueListRead,
    EInvoiceResultRequest,
    InvoiceRead,
)
from app.modules.einvoice.service import EInvoiceService
from app.shared.enums import UploadStatus
from app.shared.exceptions import (
    EInvoiceQueueItemNotFound,
    EInvoiceQueueNotRetryable,
    EInvoiceResultConflict,
    EInvoiceResultNotApplicable,
    InvoiceIncompleteForIssue,
    InvoiceNotFound,
)

router = APIRouter(prefix="/einvoice", tags=["einvoice"])
invoices_router = APIRouter(prefix="/invoices", tags=["einvoice"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role("MANAGER"))]


@invoices_router.get("/{invoice_id}", response_model=InvoiceRead, operation_id="getInvoice")
async def get_invoice(
    invoice_id: int,
    session: SessionDep,
    user: CurrentUserDep,
) -> InvoiceRead:
    """單張發票（店別範圍）。"""
    try:
        invoice = await EInvoiceService(session).get_invoice(user.store_id, invoice_id)
    except InvoiceNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return InvoiceRead.model_validate(invoice)


@router.get("/queue", response_model=EInvoiceQueueListRead, operation_id="listEInvoiceQueue")
async def list_queue(
    session: SessionDep,
    user: ManagerDep,
    status_filter: Annotated[UploadStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EInvoiceQueueListRead:
    """上傳佇列（限 MANAGER；可依狀態過濾、分頁）——供檢視待送/失敗項目。"""
    items = await EInvoiceService(session).list_queue(
        user.store_id, status=status_filter, limit=limit, offset=offset
    )
    return EInvoiceQueueListRead(
        items=[EInvoiceQueueItemRead.model_validate(item) for item in items],
        limit=limit,
        offset=offset,
    )


@router.post(
    "/queue/{queue_id}/retry",
    response_model=EInvoiceQueueItemRead,
    operation_id="retryEInvoiceQueue",
)
async def retry_queue_item(
    queue_id: int,
    session: SessionDep,
    user: ManagerDep,
) -> EInvoiceQueueItemRead:
    """重送失敗的佇列項目（限 MANAGER；FAILED→PENDING、attempts+1，不新配發票號碼）。"""
    svc = EInvoiceService(session)
    try:
        item = await svc.retry(user.store_id, queue_id)
    except EInvoiceQueueItemNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EInvoiceQueueNotRetryable as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return EInvoiceQueueItemRead.model_validate(item)


@router.post(
    "/queue/{queue_id}/result",
    response_model=EInvoiceQueueItemRead,
    operation_id="recordEInvoiceResult",
)
async def record_result(
    queue_id: int,
    payload: EInvoiceResultRequest,
    session: SessionDep,
    user: ManagerDep,
) -> EInvoiceQueueItemRead:
    """記錄平台回執並更新佇列/發票狀態（限 MANAGER）。

    成功→UPLOADED、失敗→FAILED（可再 retry）。自動解析 Turnkey 回執檔的 importer
    待收尾階段實作；此端點為手動/importer 共用的結果落庫出口。
    """
    svc = EInvoiceService(session)
    try:
        item = await svc.record_result(
            user.store_id,
            queue_id,
            success=payload.success,
            kind=payload.kind,
            status_code=payload.status_code,
            message=payload.message,
            source_ref=payload.source_ref,
            delivery_attempt=payload.delivery_attempt,
        )
    except EInvoiceQueueItemNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EInvoiceResultNotApplicable as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EInvoiceResultConflict as exc:
        # 矛盾的遲到回執：**commit 保留稽核事件**（append-only 證據不可因 409 而消失），
        # 終態未被 service 變更，再回報衝突。
        await session.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvoiceIncompleteForIssue as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await session.commit()
    return EInvoiceQueueItemRead.model_validate(item)
