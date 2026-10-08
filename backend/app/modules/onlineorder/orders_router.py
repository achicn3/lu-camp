"""線上訂單（店內端）端點（docs/44 §5.3）：今日清單、帶入結帳內容、取消、暫停／恢復接單。

店員就能用（櫃台收單）。結帳本身走既有的 POST /sales（帶 online_order_id）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.onlineorder.client import OnlineOrderClient
from app.modules.onlineorder.orders_schemas import (
    OnlineAcceptingRequest,
    OnlineCartRead,
    OnlineOrderRead,
    OnlineOrdersRead,
)
from app.modules.onlineorder.orders_service import OnlineOrdersService
from app.modules.onlineorder.router import get_online_order_client
from app.shared.exceptions import (
    DomainError,
    OnlineOrderConflict,
    OnlineOrderNotConfigured,
    OnlineOrderNotFound,
    OnlineOrderPushFailed,
)

router = APIRouter(prefix="/online-orders", tags=["online-orders"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserDep = Annotated[CurrentUser, Depends(get_current_user)]
ClientDep = Annotated[OnlineOrderClient | None, Depends(get_online_order_client)]

_STATUS: dict[type[DomainError], int] = {
    OnlineOrderNotFound: status.HTTP_404_NOT_FOUND,
    OnlineOrderConflict: status.HTTP_409_CONFLICT,
    OnlineOrderNotConfigured: status.HTTP_409_CONFLICT,
    OnlineOrderPushFailed: status.HTTP_502_BAD_GATEWAY,
}
_Errors = tuple(_STATUS)


def _http_error(exc: DomainError) -> HTTPException:
    return HTTPException(status_code=_STATUS[type(exc)], detail=str(exc))


@router.get("", response_model=OnlineOrdersRead, operation_id="listOnlineOrders")
async def list_online_orders(
    session: SessionDep, user: UserDep, client: ClientDep
) -> OnlineOrdersRead:
    overview = await OnlineOrdersService(session, client).overview(user.store_id)
    return OnlineOrdersRead.from_overview(overview)


@router.get("/{order_id}/cart", response_model=OnlineCartRead, operation_id="getOnlineOrderCart")
async def get_online_order_cart(
    order_id: int, session: SessionDep, user: UserDep, client: ClientDep
) -> OnlineCartRead:
    try:
        cart = await OnlineOrdersService(session, client).cart(user.store_id, order_id)
    except _Errors as exc:
        raise _http_error(exc) from exc
    return OnlineCartRead.from_cart(cart)


@router.post("/{order_id}/cancel", response_model=OnlineOrderRead, operation_id="cancelOnlineOrder")
async def cancel_online_order(
    order_id: int, session: SessionDep, user: UserDep, client: ClientDep
) -> OnlineOrderRead:
    try:
        order = await OnlineOrdersService(session, client).cancel(
            user.store_id, order_id, actor_user_id=user.id
        )
    except _Errors as exc:
        raise _http_error(exc) from exc
    await session.commit()
    return OnlineOrderRead.from_row(order)


@router.post(
    "/{order_id}/hand-over", response_model=OnlineOrderRead, operation_id="handOverOnlineOrder"
)
async def hand_over_online_order(
    order_id: int, session: SessionDep, user: UserDep, client: ClientDep
) -> OnlineOrderRead:
    """帶回家商品交給客人了：結單（docs/63 §13）。店員可按；重按不出錯。"""
    try:
        order = await OnlineOrdersService(session, client).hand_over(
            user.store_id, order_id, actor_user_id=user.id
        )
    except _Errors as exc:
        raise _http_error(exc) from exc
    await session.commit()
    return OnlineOrderRead.from_row(order)


@router.put("/accepting", response_model=OnlineOrdersRead, operation_id="setOnlineOrderAccepting")
async def set_online_order_accepting(
    payload: OnlineAcceptingRequest, session: SessionDep, user: UserDep, client: ClientDep
) -> OnlineOrdersRead:
    svc = OnlineOrdersService(session, client)
    try:
        await svc.set_accepting(user.store_id, payload.accepting)
    except _Errors as exc:
        raise _http_error(exc) from exc
    await session.commit()
    return OnlineOrdersRead.from_overview(await svc.overview(user.store_id))
