"""線上點餐（店內端）端點（docs/44 §5.3）：發佈菜單、狀態與桌位碼、重發桌位碼。MANAGER 專用。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import CurrentUser, require_role
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.schemas import (
    OnlineMenuPublishRead,
    OnlineOrderStatusRead,
    OnlineTableRead,
)
from app.modules.onlineorder.service import OnlineOrderService
from app.shared.exceptions import (
    OnlineOrderNotConfigured,
    OnlineOrderPushFailed,
    OnlineTableNotFound,
)

router = APIRouter(prefix="/online-order", tags=["online-order"])


def get_online_order_client() -> OnlineOrderClient | None:
    """雲端網址或密鑰沒設定＝None（發佈會回 409 說明）。測試以 dependency override 換成假的雲端。"""
    settings = get_settings()
    if not settings.online_order_base_url or not settings.online_order_secret:
        return None
    return OnlineOrderClient(settings.online_order_base_url, settings.online_order_secret)


SessionDep = Annotated[AsyncSession, Depends(get_session)]
ManagerDep = Annotated[CurrentUser, Depends(require_role("MANAGER"))]
ClientDep = Annotated[OnlineOrderClient | None, Depends(get_online_order_client)]


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, OnlineOrderNotConfigured):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, OnlineTableNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


_Errors = (OnlineOrderNotConfigured, OnlineOrderPushFailed, OnlineTableNotFound)


@router.get("/status", response_model=OnlineOrderStatusRead, operation_id="getOnlineOrderStatus")
async def get_online_order_status(
    session: SessionDep, user: ManagerDep, client: ClientDep
) -> OnlineOrderStatusRead:
    result = await OnlineOrderService(session, client).status(user.store_id)
    return OnlineOrderStatusRead.from_status(result)


@router.post("/publish", response_model=OnlineMenuPublishRead, operation_id="publishOnlineMenu")
async def publish_online_menu(
    session: SessionDep, user: ManagerDep, client: ClientDep
) -> OnlineMenuPublishRead:
    try:
        result = await OnlineOrderService(session, client).publish(
            user.store_id, actor_user_id=user.id
        )
    except _Errors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return OnlineMenuPublishRead.from_result(result)


@router.post(
    "/tables/{label}/rotate", response_model=OnlineTableRead, operation_id="rotateOnlineTableCode"
)
async def rotate_online_table_code(
    label: str, session: SessionDep, user: ManagerDep, client: ClientDep
) -> OnlineTableRead:
    try:
        link = await OnlineOrderService(session, client).rotate_table(
            user.store_id, label, actor_user_id=user.id
        )
    except _Errors as exc:
        await session.rollback()
        raise _http_error(exc) from exc
    await session.commit()
    return OnlineTableRead.from_link(link)
