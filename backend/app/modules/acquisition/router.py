"""acquisition 路由：收購/寄售入庫。I/O 與權限，orchestrate 委派 service。

整筆原子性：service 只 flush；router 成功才 commit、任何失敗先 rollback 再回錯，
確保「庫存建了但現金沒扣」之類的半套不會落地（給現金永遠在系統成功之後）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.acquisition.schemas import (
    AcquisitionCreate,
    AcquisitionRead,
    AcquisitionResult,
)
from app.modules.acquisition.service import AcquisitionService
from app.shared.exceptions import (
    AcquisitionRequiresNationalId,
    ContactNotFound,
    DomainError,
    InvalidPayoutSplit,
    NoOpenCashSession,
    StoreCreditConflict,
    StoreCreditMemberRequired,
)

router = APIRouter(prefix="/acquisitions", tags=["acquisition"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]

# 領域錯誤 → HTTP 狀態；未列出者（如直接呼叫才會遇到的 InvalidCommissionPct）視為 400。
_STATUS_BY_EXC: dict[type[DomainError], int] = {
    ContactNotFound: status.HTTP_404_NOT_FOUND,
    AcquisitionRequiresNationalId: status.HTTP_422_UNPROCESSABLE_CONTENT,
    NoOpenCashSession: status.HTTP_409_CONFLICT,
    # SC-2 撥款：拆分不合法/非會員 → 422；同來源衝突 → 409
    InvalidPayoutSplit: status.HTTP_422_UNPROCESSABLE_CONTENT,
    StoreCreditMemberRequired: status.HTTP_422_UNPROCESSABLE_CONTENT,
    StoreCreditConflict: status.HTTP_409_CONFLICT,
}


def _http_status_for(exc: DomainError) -> int:
    return _STATUS_BY_EXC.get(type(exc), status.HTTP_400_BAD_REQUEST)


@router.post(
    "",
    response_model=AcquisitionResult,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAcquisition",
)
async def create_acquisition(
    payload: AcquisitionCreate, session: SessionDep, user: CurrentUserDep
) -> AcquisitionResult:
    svc = AcquisitionService(session)
    try:
        result = await svc.create_acquisition(user.store_id, user.id, payload)
    except DomainError as exc:
        await session.rollback()
        raise HTTPException(status_code=_http_status_for(exc), detail=str(exc)) from exc
    except Exception:
        # 任何非預期錯誤也整筆回復，不留半套（再向上拋為 500）。
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
