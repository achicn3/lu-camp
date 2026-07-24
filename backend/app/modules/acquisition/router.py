"""acquisition 路由：收購/寄售入庫。I/O 與權限，orchestrate 委派 service。

整筆原子性：service 只 flush；router 成功才 commit、任何失敗先 rollback 再回錯，
確保「庫存建了但現金沒扣」之類的半套不會落地（給現金永遠在系統成功之後）。
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.modules.acquisition.schemas import (
    AcquisitionCreate,
    AcquisitionRead,
    AcquisitionResult,
    AcquisitionVoidRequest,
    AcquisitionVoidResult,
)
from app.modules.acquisition.service import AcquisitionService
from app.shared.enums import UserRole
from app.shared.exceptions import (
    AcquisitionAlreadyVoid,
    AcquisitionCreditSpent,
    AcquisitionHasSoldItems,
    AcquisitionNotFound,
    AcquisitionRequiresNationalId,
    AcquisitionVoidUnsupported,
    ContactNotFound,
    CrossStoreReference,
    DomainError,
    IdempotencyKeyConflict,
    InvalidAcquisitionCategory,
    InvalidPayoutSplit,
    NoOpenCashSession,
    SignatureContentMismatch,
    SignatureTaskConflict,
    SignatureTaskNotFound,
    SignatureTaskNotPending,
    StoreCreditConflict,
    StoreCreditMemberRequired,
)

router = APIRouter(prefix="/acquisitions", tags=["acquisition"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role(UserRole.MANAGER.value))]

# 領域錯誤 → HTTP 狀態；未列出者（如直接呼叫才會遇到的 InvalidCommissionPct）視為 400。
_STATUS_BY_EXC: dict[type[DomainError], int] = {
    ContactNotFound: status.HTTP_404_NOT_FOUND,
    AcquisitionNotFound: status.HTTP_404_NOT_FOUND,
    AcquisitionRequiresNationalId: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidAcquisitionCategory: status.HTTP_422_UNPROCESSABLE_CONTENT,
    NoOpenCashSession: status.HTTP_409_CONFLICT,
    # SC-2 撥款：拆分不合法/非會員 → 422；同來源衝突 → 409
    InvalidPayoutSplit: status.HTTP_422_UNPROCESSABLE_CONTENT,
    IdempotencyKeyConflict: status.HTTP_409_CONFLICT,
    StoreCreditMemberRequired: status.HTTP_422_UNPROCESSABLE_CONTENT,
    CrossStoreReference: status.HTTP_422_UNPROCESSABLE_CONTENT,
    StoreCreditConflict: status.HTTP_409_CONFLICT,
    # 手持切結綁定（docs/23 K4）：查無/非本會員已簽切結 → 404/422；已被綁定 → 409
    SignatureContentMismatch: status.HTTP_422_UNPROCESSABLE_CONTENT,
    SignatureTaskNotFound: status.HTTP_404_NOT_FOUND,
    SignatureTaskNotPending: status.HTTP_422_UNPROCESSABLE_CONTENT,
    SignatureTaskConflict: status.HTTP_409_CONFLICT,
    # F6.5 作廢：已作廢/含已售/購物金已花 → 409（衝突狀態，不可作廢）
    AcquisitionAlreadyVoid: status.HTTP_409_CONFLICT,
    AcquisitionHasSoldItems: status.HTTP_409_CONFLICT,
    AcquisitionCreditSpent: status.HTTP_409_CONFLICT,
    AcquisitionVoidUnsupported: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def _http_status_for(exc: DomainError) -> int:
    return _STATUS_BY_EXC.get(type(exc), status.HTTP_400_BAD_REQUEST)


async def _fail_acquisition_signature(
    session: AsyncSession,
    *,
    store_id: int,
    task_id: int | None,
    actor_user_id: int,
    reason_detail: str,
) -> None:
    """在原收購交易回滾後，以新交易封存已簽但未成交的任務。"""
    if task_id is None:
        return
    from app.modules.signing.service import SigningService

    failed = await SigningService(session).fail_signed_task_by_id(
        store_id,
        task_id,
        reason_code="ACQUISITION_FAILED",
        reason_detail=reason_detail,
        actor_user_id=actor_user_id,
    )
    if failed is not None:
        await session.commit()


@router.post(
    "",
    response_model=AcquisitionResult,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAcquisition",
)
async def create_acquisition(
    payload: AcquisitionCreate,
    session: SessionDep,
    user: CurrentUserDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=80)],
) -> AcquisitionResult:
    """建立收購單（必帶 Idempotency-Key，D-2 模式）：重試回原結果、不重複
    入庫/付現/入購物金；同 key 不同內容 409。"""
    svc = AcquisitionService(session)
    try:
        result = await svc.create_acquisition(
            user.store_id, user.id, payload, idempotency_key=idempotency_key
        )
    except DomainError as exc:
        await session.rollback()
        await _fail_acquisition_signature(
            session,
            store_id=user.store_id,
            task_id=payload.signature_task_id,
            actor_user_id=user.id,
            reason_detail=str(exc),
        )
        raise HTTPException(status_code=_http_status_for(exc), detail=str(exc)) from exc
    except Exception:
        # 收購只涉及同一筆本機 DB 交易；回滾後可確定未成交，已簽任務必須封存為 FAILED。
        await session.rollback()
        try:
            await _fail_acquisition_signature(
                session,
                store_id=user.store_id,
                task_id=payload.signature_task_id,
                actor_user_id=user.id,
                reason_detail="收購發生未預期的後端錯誤",
            )
        except Exception:
            # 保留原始例外；若資料庫本身不可用，後續 sweeper/稽核可處理殘留任務。
            await session.rollback()
        raise
    await session.commit()
    return result


@router.get("/{acquisition_id}", response_model=AcquisitionRead, operation_id="getAcquisition")
async def get_acquisition(
    acquisition_id: int, session: SessionDep, user: CurrentUserDep
) -> AcquisitionRead:
    acquisition = await AcquisitionService(session).get_acquisition(user.store_id, acquisition_id)
    if acquisition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="找不到收購單")
    return AcquisitionRead.from_model(acquisition)


@router.post(
    "/{acquisition_id}/void",
    response_model=AcquisitionVoidResult,
    operation_id="voidAcquisition",
)
async def void_acquisition(
    acquisition_id: int,
    payload: AcquisitionVoidRequest,
    session: SessionDep,
    user: ManagerDep,
) -> AcquisitionVoidResult:
    """作廢收購（限 MANAGER，F6.5）：對稱反轉庫存/現金/購物金，全程稽核；整筆原子、失敗回滾。

    冪等/併發：以收購列鎖＋ voided_at 狀態為準——重複作廢回 409（不雙重沖回）。
    已作廢/含已售庫存/購物金已花 → 409；付現但無開帳 → 409；找不到 → 404；非 MANAGER → 403。
    """
    svc = AcquisitionService(session)
    try:
        acquisition = await svc.void_acquisition(
            user.store_id, acquisition_id, actor_user_id=user.id, reason=payload.reason
        )
    except DomainError as exc:
        await session.rollback()
        raise HTTPException(status_code=_http_status_for(exc), detail=str(exc)) from exc
    except Exception:
        await session.rollback()
        raise
    await session.commit()
    assert acquisition.voided_at is not None  # void_acquisition 成功必已設
    return AcquisitionVoidResult(
        acquisition_id=acquisition.id,
        voided_at=acquisition.voided_at,
        reversed_cash=acquisition.payout_cash_amount or Decimal(0),
        reversed_credit=acquisition.payout_credit_cash_equivalent or Decimal(0),
    )
