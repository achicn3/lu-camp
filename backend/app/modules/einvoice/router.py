"""einvoice 路由（T14 殼）：發票查詢、上傳佇列檢視/重送、回執記錄。

發票的建立/開立由未來的銷售結帳流程（einvoice_enabled=true 時）於原子交易內經 service
進行；本 router 只暴露查詢、MANAGER 可見的佇列與重送、回執記錄。實際拋檔/Turnkey 上傳
由收尾階段（憑證/主機到位）搭配 XSD-backed 序列化器與排程接手。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings as get_app_settings
from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.einvoice.amego import AmegoClient, HttpxAmegoTransport
from app.modules.einvoice.schemas import (
    EInvoiceQueueItemRead,
    EInvoiceQueueListRead,
    EInvoiceResultRequest,
    InvoiceRead,
)
from app.modules.einvoice.service import EInvoiceService
from app.modules.store.service import StoreService
from app.shared.enums import UploadStatus
from app.shared.exceptions import (
    AmegoIssueFailed,
    AmegoNotConfigured,
    AmegoTransportError,
    EInvoiceQueueItemNotFound,
    EInvoiceQueueNotDroppable,
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


async def _amego_client(session: AsyncSession, store_id: int) -> AmegoClient:
    """組 Amego 客戶端：賣方統編＝stores.tax_id、App Key＝環境變數（docs/24）。"""
    cfg = get_app_settings()
    store = await StoreService(session).get_receipt_header(store_id)
    return AmegoClient(
        seller_tax_id=store.tax_id or "",
        app_key=cfg.amego_app_key,
        transport=HttpxAmegoTransport(),
        base_url=cfg.amego_api_base,
    )


@router.post(
    "/sales/{sale_id}/issue",
    response_model=InvoiceRead,
    operation_id="issueEInvoiceForSale",
)
async def issue_invoice_for_sale(
    sale_id: int,
    session: SessionDep,
    user: CurrentUserDep,
) -> InvoiceRead:
    """POS 結帳後開立（docs/24）：把該銷售的發票上送 Amego、回開立後發票（冪等）。

    已開立 → 直接回原發票（重試/重印取號）。平台拒絕 → 502（佇列 FAILED 可重試）；
    傳輸中斷（結果未知）→ 502（已認領，下次呼叫自動對帳）；未設定憑證 → 409。
    """
    svc = EInvoiceService(session)
    try:
        client = await _amego_client(session, user.store_id)
        invoice = await svc.issue_for_sale(user.store_id, sale_id, client=client)
    except AmegoNotConfigured as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvoiceNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (EInvoiceQueueNotDroppable, EInvoiceQueueItemNotFound, EInvoiceQueueNotRetryable) as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (AmegoIssueFailed, AmegoTransportError) as exc:
        # send_via_amego 自管交易：認領/FAILED 已 commit，此處僅回報（不 rollback 已存事實）。
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    await session.commit()
    return InvoiceRead.model_validate(invoice)


@router.post(
    "/queue/{queue_id}/send",
    response_model=EInvoiceQueueItemRead,
    operation_id="sendEInvoiceQueueItem",
)
async def send_queue_item(
    queue_id: int,
    session: SessionDep,
    user: ManagerDep,
) -> EInvoiceQueueItemRead:
    """把 PENDING 佇列列上送 Amego（限 MANAGER；開立/作廢/折讓共用出口，docs/24）。

    成功 → UPLOADED；平台拒絕 → 列轉 FAILED 後回 200（front 以 status/last_error 呈現，
    可 retry）；傳輸中斷 → 502（已認領，重呼自動對帳）。
    """
    svc = EInvoiceService(session)
    try:
        client = await _amego_client(session, user.store_id)
        item = await svc.send_via_amego(user.store_id, queue_id, client=client)
    except AmegoNotConfigured as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EInvoiceQueueItemNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EInvoiceQueueNotDroppable as exc:
        await session.rollback()  # 守衛擋於認領前，無已存副作用
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EInvoiceResultConflict as exc:
        # 事件已留稽核（service 已 commit），僅回報衝突、不 rollback。
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AmegoTransportError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    await session.commit()
    return EInvoiceQueueItemRead.model_validate(item)


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
        # 規則：**回執事件一旦落庫、永不回滾**（Codex 第八輪）——未認領的回執本身就是
        # 值得稽核的異常證據；commit 保留事件後回 409（佇列/發票未被 service 變更）。
        await session.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EInvoiceResultConflict as exc:
        # 矛盾的遲到回執：commit 保留稽核事件（append-only 證據不可因 409 而消失），
        # 終態未被 service 變更，再回報衝突。
        await session.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvoiceIncompleteForIssue as exc:
        # 平台「成功」回執不可因本地欄位不齊而消失（Codex 第八輪 high）：commit 保留事件
        # （佇列維持 PENDING、發票未動），操作員補齊字軌/日期/時間/隨機碼後重送回執即收斂；
        # 稽核軌跡完整證明平台已核可。
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    await session.commit()
    return EInvoiceQueueItemRead.model_validate(item)
