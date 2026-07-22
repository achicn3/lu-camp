"""storecredit 路由（SC-1）：餘額/歷史查詢＋人工校正（docs/16 §4）。

入帳/扣抵/沖正不開 API——由收購（SC-2）/銷售（SC-3）流程於原子交易內經
service 進行；API 只暴露查詢與 MANAGER 校正。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user, require_role
from app.core.time import store_date, utc_now
from app.modules.storecredit.schemas import (
    PremiumSuggestionResponse,
    StoreCreditAdjustRequest,
    StoreCreditBalanceRead,
    StoreCreditEntryRead,
)
from app.modules.storecredit.service import StoreCreditService
from app.modules.storecredit.suggestion_service import PremiumSuggestionService
from app.shared.exceptions import (
    InsufficientStoreCredit,
    StoreCreditConflict,
    StoreCreditMemberRequired,
)

router = APIRouter(prefix="/contacts/{contact_id}/store-credit", tags=["store-credit"])
# 店層級（非 contact 範圍）的購物金端點；與上方 router 分開掛載。
store_router = APIRouter(prefix="/store-credit", tags=["store-credit"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
ManagerDep = Annotated[CurrentUser, Depends(require_role("MANAGER"))]


@router.get("", response_model=StoreCreditBalanceRead, operation_id="getStoreCredit")
async def get_store_credit(
    contact_id: int,
    session: SessionDep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> StoreCreditBalanceRead:
    """餘額＋異動歷史（分頁，新到舊）；店別範圍（§4）。"""
    svc = StoreCreditService(session)
    balance = await svc.get_balance(user.store_id, contact_id)
    entries = await svc.list_entries(user.store_id, contact_id, limit=limit, offset=offset)
    return StoreCreditBalanceRead(
        contact_id=contact_id,
        balance=balance,
        entries=[StoreCreditEntryRead.model_validate(entry) for entry in entries],
    )


@router.post(
    "/adjustments",
    response_model=StoreCreditEntryRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="adjustStoreCredit",
)
async def adjust_store_credit(
    contact_id: int,
    payload: StoreCreditAdjustRequest,
    session: SessionDep,
    user: ManagerDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=80)],
) -> StoreCreditEntryRead:
    """人工校正（限 MANAGER、事由必填、寫稽核；餘額不可為負；冪等鍵必帶——
    重試/雙擊不得重複改負債）。"""
    svc = StoreCreditService(session)
    try:
        entry = await svc.adjust(
            user.store_id,
            contact_id,
            amount=payload.amount,
            reason=payload.reason,
            created_by=user.id,
            idempotency_key=idempotency_key,
        )
    except InsufficientStoreCredit as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except StoreCreditMemberRequired as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except StoreCreditConflict as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return StoreCreditEntryRead.model_validate(entry)


@store_router.get(
    "/premium-suggestion/today",
    response_model=PremiumSuggestionResponse,
    operation_id="storeCreditPremiumSuggestionToday",
)
async def premium_suggestion_today(
    session: SessionDep,
    user: CurrentUserDep,
) -> PremiumSuggestionResponse:
    """當日溢價建議值（docs/16 §6.2）：當日首次讀取時 lazy 計算並冪等落庫，否則回既有快照。

    建議值僅供面板顯示與人工確認，**永不自動生效**（POS 開帳面板/設定頁皆用此端點）。
    """
    now = utc_now()
    log = await PremiumSuggestionService(session).suggestion_today(
        user.store_id, today=store_date(now), now=now
    )
    await session.commit()
    return PremiumSuggestionResponse.model_validate(log)
