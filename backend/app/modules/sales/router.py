"""sales 路由：POS 結帳、查詢、作廢、補印明細。I/O 與權限，業務邏輯委派 service。

寫入端點成功才 commit、領域錯誤先 rollback 再回可辨識的 HTTP 錯誤（整筆原子性）。
POST /sales 帶 Idempotency-Key 標頭：同 key 重送只建一筆、回原單（D-2，防網路重試重複建單/收錢）；
並行重送的競態由 (store_id, idempotency_key) 唯一約束擋下，撞到時改回原單。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.core.time import AwareDateTime
from app.modules.sales.linepay import LinePayClient, linepay_client_from_config
from app.modules.sales.schemas import (
    LinePayRefundAttemptRead,
    LinePayRefundResolveRequest,
    SaleCreateRequest,
    SaleQuoteLineRead,
    SaleQuoteRequest,
    SaleQuoteResponse,
    SaleRead,
    SaleSummaryRead,
)
from app.modules.sales.service import SalesService
from app.shared.enums import UserRole
from app.shared.exceptions import (
    CrossStoreReference,
    DomainError,
    EInvoiceSettingsChanged,
    EmptySale,
    IdempotencyKeyConflict,
    InsufficientStock,
    InsufficientStoreCredit,
    InvalidSaleTender,
    InvalidStateTransition,
    LinePayChargeFailed,
    LinePayRefundAmbiguous,
    ManualRefundRequired,
    MemberPointsAdjustFailed,
    MenuItemNotFound,
    MenuItemUnavailable,
    NoOpenCashSession,
    SaleAlreadyVoid,
    SaleHasReturns,
    SaleItemNotFound,
    SaleLineInvalid,
    SignatureContentMismatch,
    SignatureTaskConflict,
    SignatureTaskNotFound,
    SignatureTaskNotPending,
    StoreCreditConflict,
    StoreCreditMemberRequired,
)

router = APIRouter(prefix="/sales", tags=["sales"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role(UserRole.MANAGER.value))]

# 領域錯誤 → HTTP 狀態；未列出者視為 400。
_STATUS_BY_EXC: dict[type[DomainError], int] = {
    NoOpenCashSession: status.HTTP_409_CONFLICT,
    InsufficientStock: status.HTTP_409_CONFLICT,
    InsufficientStoreCredit: status.HTTP_409_CONFLICT,
    StoreCreditConflict: status.HTTP_409_CONFLICT,
    InvalidStateTransition: status.HTTP_409_CONFLICT,
    SaleAlreadyVoid: status.HTTP_409_CONFLICT,
    IdempotencyKeyConflict: status.HTTP_409_CONFLICT,
    EInvoiceSettingsChanged: status.HTTP_409_CONFLICT,
    # LINE Pay 拒付/未設定/未啟用（fail-closed，整筆不成立）→ 402 Payment Required。
    LinePayChargeFailed: status.HTTP_402_PAYMENT_REQUIRED,
    # 台灣Pay 作廢須先手動退款確認；LINE Pay 退款上次結果未定須人工對帳 → 409（前置未滿足）。
    ManualRefundRequired: status.HTTP_409_CONFLICT,
    LinePayRefundAmbiguous: status.HTTP_409_CONFLICT,
    SaleItemNotFound: status.HTTP_404_NOT_FOUND,
    MenuItemNotFound: status.HTTP_404_NOT_FOUND,
    MenuItemUnavailable: status.HTTP_409_CONFLICT,
    CrossStoreReference: status.HTTP_422_UNPROCESSABLE_CONTENT,
    SaleLineInvalid: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidSaleTender: status.HTTP_422_UNPROCESSABLE_CONTENT,
    StoreCreditMemberRequired: status.HTTP_422_UNPROCESSABLE_CONTENT,
    EmptySale: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # 購物金扣抵手持簽署（docs/23 K5）
    SignatureContentMismatch: status.HTTP_422_UNPROCESSABLE_CONTENT,
    SignatureTaskNotFound: status.HTTP_404_NOT_FOUND,
    SignatureTaskNotPending: status.HTTP_422_UNPROCESSABLE_CONTENT,
    SignatureTaskConflict: status.HTTP_409_CONFLICT,
}


def _http_status_for(exc: DomainError) -> int:
    return _STATUS_BY_EXC.get(type(exc), status.HTTP_400_BAD_REQUEST)


def _linepay_client() -> LinePayClient | None:
    """依 config 建 LINE Pay 客戶端（共用工廠；未設定 → None，create_sale/void fail-closed）。"""
    return linepay_client_from_config()


@router.post(
    "",
    response_model=SaleRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSale",
)
async def create_sale(
    payload: SaleCreateRequest,
    session: SessionDep,
    user: CurrentUserDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=80)],
) -> SaleRead:
    svc = SalesService(session)
    try:
        sale = await svc.create_sale(
            user.store_id,
            user.id,
            lines=payload.to_inputs(),
            buyer_contact_id=payload.buyer_contact_id,
            tenders=payload.to_tender_inputs(),
            idempotency_key=idempotency_key,
            signature_task_id=payload.signature_task_id,
            invoice_info=payload.to_invoice_info(),
            expected_einvoice_enabled=payload.expected_einvoice_enabled,
            require_einvoice_confirmation=True,  # HTTP 邊界強制宣告發票設定狀態（docs/24）
            linepay_client=_linepay_client(),
        )
    except IntegrityError as exc:
        await session.rollback()
        # 一份購物金扣抵簽署至多綁一筆銷售（docs/23 K5，D3）：撞單次使用唯一約束。並發首寫
        # 競態（前置回放時尚無既有列、插入互撞）落到這裡——贏家已可見：指紋相符回原單回放、
        # 不符/已作廢 → 409（Codex K5 第一輪；同 K4 第九輪模式）。
        if "uq_sales_signature_task" in str(exc.orig):
            assert payload.signature_task_id is not None  # 無簽署不可能撞此約束
            try:
                bound_sale = await svc.find_signature_replay(
                    user.store_id,
                    payload.signature_task_id,
                    lines=payload.to_inputs(),
                    buyer_contact_id=payload.buyer_contact_id,
                    tenders=payload.to_tender_inputs(),
                    invoice_info=payload.to_invoice_info(),
                )
            except SignatureTaskConflict as conflict:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=str(conflict)
                ) from conflict
            lines = await svc.get_lines(bound_sale.id)
            tenders = await svc.get_tenders(bound_sale.id)
            return SaleRead.build(bound_sale, lines, tenders)
        # 僅處理 idempotency 唯一約束違反（並行重送）；其他完整性錯誤不吞、照常往外拋。
        if "uq_sales_store_idempotency_key" not in str(exc.orig):
            raise
        # 與 pre-check 共用的 fingerprint 檢查：同 key 不同購物車 → 409，不靜默丟單。
        try:
            existing = await svc.find_idempotent_replay(
                user.store_id,
                idempotency_key,
                lines=payload.to_inputs(),
                buyer_contact_id=payload.buyer_contact_id,
                tenders=payload.to_tender_inputs(),
                invoice_info=payload.to_invoice_info(),
            )
        except IdempotencyKeyConflict as conflict:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(conflict)
            ) from conflict
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="結帳衝突，請重試"
            ) from exc
        lines = await svc.get_lines(existing.id)
        tenders = await svc.get_tenders(existing.id)
        return SaleRead.build(existing, lines, tenders)
    except DomainError as exc:
        await session.rollback()
        raise HTTPException(status_code=_http_status_for(exc), detail=str(exc)) from exc
    except Exception:
        await session.rollback()
        raise
    lines = await svc.get_lines(sale.id)
    tenders = await svc.get_tenders(sale.id)
    await session.commit()
    return SaleRead.build(sale, lines, tenders)


@router.post("/quote", response_model=SaleQuoteResponse, operation_id="quoteSale")
async def quote_sale(
    payload: SaleQuoteRequest, session: SessionDep, user: CurrentUserDep
) -> SaleQuoteResponse:
    """結帳前試算（docs/21 C2b）：套生效活動回折後總額與各行折讓。唯讀——不扣庫存、不收款。

    供 POS 顯示折後價並送對齊折後總額的收款（避免前端自算金額導致收款不對齊 → 422）。
    """
    try:
        quote = await SalesService(session).quote_sale(
            user.store_id,
            lines=payload.to_inputs(),
            buyer_contact_id=payload.buyer_contact_id,
        )
    except DomainError as exc:
        raise HTTPException(status_code=_http_status_for(exc), detail=str(exc)) from exc
    return SaleQuoteResponse(
        total=quote.total,
        campaign_id=quote.campaign_id,
        campaign_name=quote.campaign_name,
        lines=[
            SaleQuoteLineRead(
                line_type=ql.line_type,
                description=ql.description,
                qty=ql.qty,
                unit_price=ql.unit_price,
                line_total=ql.line_total,
                original_unit_price=ql.original_unit_price,
                discount_amount=ql.discount_amount,
            )
            for ql in quote.lines
        ],
        food_subtotal=quote.food_subtotal,
        store_credit_max=quote.store_credit_max,
        store_credit_min_spend=quote.store_credit_min_spend,
    )


@router.get("", response_model=list[SaleSummaryRead], operation_id="listSales")
async def list_sales(
    session: SessionDep,
    user: CurrentUserDep,
    date_from: Annotated[AwareDateTime | None, Query(alias="from")] = None,
    date_to: Annotated[AwareDateTime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SaleSummaryRead]:
    sales = await SalesService(session).list_sales(
        user.store_id, date_from=date_from, date_to=date_to, limit=limit, offset=offset
    )
    return [SaleSummaryRead.model_validate(sale) for sale in sales]


@router.get("/{sale_id}", response_model=SaleRead, operation_id="getSale")
async def get_sale(sale_id: int, session: SessionDep, user: CurrentUserDep) -> SaleRead:
    svc = SalesService(session)
    sale = await svc.get_sale(user.store_id, sale_id)
    if sale is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到銷售單")
    lines = await svc.get_lines(sale.id)
    tenders = await svc.get_tenders(sale.id)
    # 退貨頁需要每行已退量算可退餘量（跨模組經 returns service，§2）
    from app.modules.returns.service import ReturnsService

    returned = await ReturnsService(session).returned_qty_for_sale(user.store_id, sale.id)
    return SaleRead.build(sale, lines, tenders, returned_by_line=returned)


@router.post("/{sale_id}/void", response_model=SaleRead, operation_id="voidSale")
async def void_sale(
    sale_id: int,
    session: SessionDep,
    user: ManagerDep,
    manual_refund_ack: Annotated[bool, Query()] = False,
) -> SaleRead:
    svc = SalesService(session)
    sale = await svc.get_sale(user.store_id, sale_id)
    if sale is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到銷售單")
    try:
        voided = await svc.void_sale(
            sale,
            user.id,
            linepay_client=_linepay_client(),
            manual_refund_ack=manual_refund_ack,
        )
    except (SaleAlreadyVoid, SaleHasReturns) as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except MemberPointsAdjustFailed as exc:
        # 點數沖回失敗（餘額異常低於該筆累積）→ 整筆回滾、409 域錯誤，不冒 500。
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DomainError as exc:
        # 購物金沖回等域錯誤（StoreCreditConflict 等）→ 整筆回滾、可辨識 HTTP。
        await session.rollback()
        raise HTTPException(status_code=_http_status_for(exc), detail=str(exc)) from exc
    lines = await svc.get_lines(voided.id)
    tenders = await svc.get_tenders(voided.id)
    await session.commit()
    return SaleRead.build(voided, lines, tenders)


@router.post("/{sale_id}/print-detail", response_model=SaleRead, operation_id="printSaleDetail")
async def print_sale_detail(sale_id: int, session: SessionDep, user: CurrentUserDep) -> SaleRead:
    svc = SalesService(session)
    sale = await svc.get_sale(user.store_id, sale_id)
    if sale is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到銷售單")
    await svc.record_print_detail(sale, user.id)
    lines = await svc.get_lines(sale.id)
    tenders = await svc.get_tenders(sale.id)
    await session.commit()
    return SaleRead.build(sale, lines, tenders)


@router.get(
    "/linepay-refunds/pending",
    response_model=list[LinePayRefundAttemptRead],
    operation_id="listPendingLinePayRefunds",
)
async def list_pending_linepay_refunds(
    session: SessionDep, user: ManagerDep
) -> list[LinePayRefundAttemptRead]:
    """未定 LINE Pay 退款（退款對帳頁；docs/30 finding #3）：店長確認/解決卡住的退款。"""
    attempts = await SalesService(session).list_pending_linepay_refunds(user.store_id)
    return [LinePayRefundAttemptRead.model_validate(a) for a in attempts]


@router.post(
    "/linepay-refunds/{attempt_id}/resolve",
    response_model=LinePayRefundAttemptRead,
    operation_id="resolveLinePayRefund",
)
async def resolve_linepay_refund(
    attempt_id: int,
    payload: LinePayRefundResolveRequest,
    session: SessionDep,
    user: ManagerDep,
) -> LinePayRefundAttemptRead:
    """人工解決未定退款（docs/30 finding #3）：SUCCEEDED＝已於後台確認退款、FAILED＝確認未退款。"""
    svc = SalesService(session)
    try:
        attempt = await svc.resolve_linepay_refund(
            user.store_id, attempt_id, resolution=payload.resolution, actor_user_id=user.id
        )
    except DomainError as exc:
        await session.rollback()
        raise HTTPException(status_code=_http_status_for(exc), detail=str(exc)) from exc
    await session.commit()
    return LinePayRefundAttemptRead.model_validate(attempt)
