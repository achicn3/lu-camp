"""收購佇列路由（docs/42）：報到收件、估價、叫號確認、取消。

店員即可操作（現場作業，卡權限反而礙事）；`KIOSK` 由 `get_current_user` 中央守衛擋掉。
只做 I/O 與驗證，業務規則在 service。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.intake.schemas import (
    IntakeBatchCreateRequest,
    IntakeBatchRead,
    IntakeCancelRequest,
    IntakeDispositionRequest,
    IntakeLineCreateRequest,
    IntakeLineFields,
    IntakeLineRead,
    IntakePayRequest,
    IntakeSignatureRead,
    IntakeSignatureRequest,
)
from app.modules.intake.service import IntakeService
from app.shared.exceptions import (
    AcquisitionRequiresNationalId,
    ContactNotFound,
    DomainError,
    IdempotencyKeyConflict,
    IntakeBatchNotFound,
    IntakeConflict,
    InvalidIntakeLine,
    InvalidPayoutSplit,
    NoOpenCashSession,
    SignatureContentMismatch,
    SignatureTaskConflict,
    SignatureTaskNotFound,
    SignatureTaskNotPending,
    StoreCreditConflict,
    StoreCreditMemberRequired,
)

router = APIRouter(prefix="/intake-batches", tags=["intake"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[CurrentUser, Depends(get_current_user)]

_STATUS: dict[type[DomainError], int] = {
    IntakeBatchNotFound: status.HTTP_404_NOT_FOUND,
    IntakeConflict: status.HTTP_409_CONFLICT,
    InvalidIntakeLine: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # 付款沿用收購流程（docs/42 §6）：沒開帳、簽署狀態不對 → 409；撥款/身分資料不合 → 422
    ContactNotFound: status.HTTP_404_NOT_FOUND,
    NoOpenCashSession: status.HTTP_409_CONFLICT,
    SignatureContentMismatch: status.HTTP_409_CONFLICT,
    SignatureTaskNotFound: status.HTTP_409_CONFLICT,
    SignatureTaskNotPending: status.HTTP_409_CONFLICT,
    SignatureTaskConflict: status.HTTP_409_CONFLICT,
    IdempotencyKeyConflict: status.HTTP_409_CONFLICT,
    StoreCreditConflict: status.HTTP_409_CONFLICT,
    InvalidPayoutSplit: status.HTTP_422_UNPROCESSABLE_CONTENT,
    StoreCreditMemberRequired: status.HTTP_422_UNPROCESSABLE_CONTENT,
    AcquisitionRequiresNationalId: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


@asynccontextmanager
async def _write(session: AsyncSession) -> AsyncIterator[None]:
    """寫入端點：領域錯誤轉 HTTP 並回滾；成功才 commit（get_session 不自動 commit）。"""
    try:
        yield
    except DomainError as exc:
        await session.rollback()
        code = _STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST)
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    await session.commit()


async def _read_batch(session: AsyncSession, store_id: int, batch_id: int) -> IntakeBatchRead:
    service = IntakeService(session)
    try:
        batch = await service.get_batch(store_id, batch_id)
    except IntakeBatchNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return await service.to_read(store_id, batch)


@router.post(
    "",
    response_model=IntakeBatchRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createIntakeBatch",
)
async def create_intake_batch(
    payload: IntakeBatchCreateRequest, session: SessionDep, user: AuthDep
) -> IntakeBatchRead:
    """報到收件：建立批次、配當日 A 編號（同店同日從 A001 起）。"""
    async with _write(session):
        batch = await IntakeService(session).create_batch(
            user.store_id,
            contact_id=payload.contact_id,
            declared_item_count=payload.declared_item_count,
            note=payload.note,
            actor_user_id=user.id,
        )
    return await _read_batch(session, user.store_id, batch.id)


@router.get("", response_model=list[IntakeBatchRead], operation_id="listIntakeBatches")
async def list_intake_batches(
    session: SessionDep,
    user: AuthDep,
    include_closed: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[IntakeBatchRead]:
    """佇列：預設只列還沒處理完的（到簽署為止），先到先處理；`include_closed` 連已結束的一起列。"""
    service = IntakeService(session)
    batches = await service.list_batches(
        user.store_id, include_closed=include_closed, limit=limit, offset=offset
    )
    return await service.to_reads(user.store_id, batches)


@router.get("/{batch_id}", response_model=IntakeBatchRead, operation_id="getIntakeBatch")
async def get_intake_batch(batch_id: int, session: SessionDep, user: AuthDep) -> IntakeBatchRead:
    return await _read_batch(session, user.store_id, batch_id)


@router.post(
    "/{batch_id}/lines",
    response_model=IntakeLineRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="addIntakeLine",
)
async def add_intake_line(
    batch_id: int, payload: IntakeLineCreateRequest, session: SessionDep, user: AuthDep
) -> IntakeLineRead:
    """新增一列估價（隨時存檔，可中途離開再回來）。"""
    async with _write(session):
        line = await IntakeService(session).add_line(user.store_id, batch_id, payload)
    return IntakeLineRead.model_validate(line, from_attributes=True)


@router.patch(
    "/{batch_id}/lines/{line_id}", response_model=IntakeLineRead, operation_id="updateIntakeLine"
)
async def update_intake_line(
    batch_id: int, line_id: int, payload: IntakeLineFields, session: SessionDep, user: AuthDep
) -> IntakeLineRead:
    """修改估價列（只改有帶的欄位）；簽署以前都能改，含叫號議價時改成交價。"""
    async with _write(session):
        line = await IntakeService(session).update_line(user.store_id, batch_id, line_id, payload)
    return IntakeLineRead.model_validate(line, from_attributes=True)


@router.delete(
    "/{batch_id}/lines/{line_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteIntakeLine",
)
async def delete_intake_line(
    batch_id: int, line_id: int, session: SessionDep, user: AuthDep
) -> Response:
    """刪掉估價中打錯的列；估完後不能刪（改用處置）。"""
    async with _write(session):
        await IntakeService(session).delete_line(user.store_id, batch_id, line_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{batch_id}/ready", response_model=IntakeBatchRead, operation_id="markIntakeReady")
async def mark_intake_ready(batch_id: int, session: SessionDep, user: AuthDep) -> IntakeBatchRead:
    """估完 → 待確認（等叫號議價）。"""
    async with _write(session):
        await IntakeService(session).mark_ready(user.store_id, batch_id)
    return await _read_batch(session, user.store_id, batch_id)


@router.patch(
    "/{batch_id}/lines/{line_id}/disposition",
    response_model=IntakeLineRead,
    operation_id="setIntakeDisposition",
)
async def set_intake_disposition(
    batch_id: int,
    line_id: int,
    payload: IntakeDispositionRequest,
    session: SessionDep,
    user: AuthDep,
) -> IntakeLineRead:
    """叫號時逐列處置（可部分接受）；沒成交的件是否已交還客人一起記。"""
    async with _write(session):
        line = await IntakeService(session).set_disposition(
            user.store_id, batch_id, line_id, payload
        )
    return IntakeLineRead.model_validate(line, from_attributes=True)


@router.post("/{batch_id}/cancel", response_model=IntakeBatchRead, operation_id="cancelIntakeBatch")
async def cancel_intake_batch(
    batch_id: int, payload: IntakeCancelRequest, session: SessionDep, user: AuthDep
) -> IntakeBatchRead:
    """客人放棄整批（簽署以前）；列不刪，東西有沒有領回逐列記。"""
    async with _write(session):
        await IntakeService(session).cancel(
            user.store_id, batch_id, reason=payload.reason, actor_user_id=user.id
        )
    return await _read_batch(session, user.store_id, batch_id)


@router.post(
    "/{batch_id}/signature",
    response_model=IntakeSignatureRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="requestIntakeSignature",
)
async def request_intake_signature(
    batch_id: int, payload: IntakeSignatureRequest, session: SessionDep, user: AuthDep
) -> IntakeSignatureRead:
    """整批要付錢的商品送到顧客螢幕給客人簽一次切結（寄售不在內）。"""
    async with _write(session):
        task = await IntakeService(session).request_signature(
            user.store_id, batch_id, terminal_id=payload.terminal_id, actor_user_id=user.id
        )
        task_id = task.id
    return IntakeSignatureRead(signature_task_id=task_id)


@router.post("/{batch_id}/pay", response_model=IntakeBatchRead, operation_id="payIntakeBatch")
async def pay_intake_batch(
    batch_id: int, payload: IntakePayRequest, session: SessionDep, user: AuthDep
) -> IntakeBatchRead:
    """付款：成立收購、商品建成「待整理」；已付款再按回原結果（不重複付錢）。"""
    async with _write(session):
        await IntakeService(session).pay(
            user.store_id, batch_id, payout_method=payload.payout_method, actor_user_id=user.id
        )
    return await _read_batch(session, user.store_id, batch_id)
