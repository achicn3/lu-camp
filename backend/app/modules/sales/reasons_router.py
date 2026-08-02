"""贈品／折扣原因代碼的讀取路由。

POS 的贈品與折扣對話框需要選單；管理（新增／停用）屬後台，另行提供。
只回**啟用中**的原因：停用的原因仍留在歷史單據上（單據存名稱快照），但不再能被選用。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.sales.schemas import ReasonRead
from app.modules.sales.service import SalesService

router = APIRouter(tags=["sales"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


@router.get(
    "/gift-reasons",
    response_model=list[ReasonRead],
    operation_id="listGiftReasons",
)
async def list_gift_reasons(session: SessionDep, user: CurrentUserDep) -> list[ReasonRead]:
    reasons = await SalesService(session).list_gift_reasons(user.store_id)
    return [ReasonRead.model_validate(reason, from_attributes=True) for reason in reasons]


@router.get(
    "/discount-reasons",
    response_model=list[ReasonRead],
    operation_id="listDiscountReasons",
)
async def list_discount_reasons(session: SessionDep, user: CurrentUserDep) -> list[ReasonRead]:
    reasons = await SalesService(session).list_discount_reasons(user.store_id)
    return [ReasonRead.model_validate(reason, from_attributes=True) for reason in reasons]
