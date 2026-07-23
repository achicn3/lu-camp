"""客顯裝置 cookie session 與店務端櫃檯配對路由。"""

import asyncio
import json
import logging
import math
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.customerdisplay.models import CartSession, KioskDevice, PosTerminal
from app.modules.customerdisplay.schemas import (
    CartCancelRequest,
    CartSessionRead,
    CartSnapshotRead,
    CartUpsertRequest,
    KioskDeviceLoginRequest,
    KioskDeviceRead,
    KioskDeviceSessionRead,
    KioskHeartbeatRead,
    KioskHeartbeatRequest,
    KioskSummary,
    TerminalCreateRequest,
    TerminalPairRequest,
    TerminalRead,
    TerminalSummary,
    TerminalUnpairRequest,
)
from app.modules.customerdisplay.service import (
    CartSessionConflict,
    CartSessionInvalid,
    CustomerDisplayService,
    DevicePrincipal,
    InvalidCsrfToken,
    InvalidDeviceSession,
    InvalidKioskCredentials,
    PairingConflict,
    TerminalNotFound,
)
from app.modules.user.router import ThrottleDep
from app.shared.exceptions import DomainError

KIOSK_COOKIE = "lu_camp_kiosk_session"
KIOSK_COOKIE_PATH = "/api/v1/kiosk"
KIOSK_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
SSE_HEARTBEAT_SECONDS = 15
SSE_POLL_SECONDS = 1
_security_log = logging.getLogger("app.security")

staff_router = APIRouter(prefix="/customer-display", tags=["customer-display"])
kiosk_router = APIRouter(prefix="/kiosk", tags=["kiosk-device"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
StaffDep = Annotated[CurrentUser, Depends(get_current_user)]


def _trusted_origins() -> set[str]:
    return {origin.strip() for origin in get_settings().cors_origins.split(",") if origin.strip()}


def _require_trusted_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None or origin not in _trusted_origins():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不允許的請求來源",
        )


async def get_kiosk_principal(
    session: SessionDep,
    raw_session: Annotated[str | None, Cookie(alias=KIOSK_COOKIE)] = None,
) -> DevicePrincipal:
    try:
        return await CustomerDisplayService(session).authenticate_device_session(raw_session)
    except InvalidDeviceSession as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


KioskPrincipalDep = Annotated[DevicePrincipal, Depends(get_kiosk_principal)]


async def require_kiosk_csrf(
    request: Request,
    session: SessionDep,
    principal: KioskPrincipalDep,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> DevicePrincipal:
    _require_trusted_origin(request)
    try:
        CustomerDisplayService(session).verify_csrf(principal, csrf_token)
    except InvalidCsrfToken as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return principal


KioskMutationDep = Annotated[DevicePrincipal, Depends(require_kiosk_csrf)]


def _sse_event(event: str, data: dict[str, object], *, event_id: str) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event}\ndata: {payload}\n\n"


def _terminal_summary(terminal: PosTerminal | None) -> TerminalSummary | None:
    if terminal is None:
        return None
    return TerminalSummary(id=terminal.id, name=terminal.name)


def _terminal_read(terminal: PosTerminal, device: KioskDevice | None) -> TerminalRead:
    return TerminalRead(
        id=terminal.id,
        installation_id=terminal.installation_id,
        name=terminal.name,
        paired_kiosk=(
            KioskSummary(
                id=device.id,
                label=device.label,
                online=CustomerDisplayService.kiosk_is_online(device),
                last_seen_at=device.last_seen_at,
                current_session_id=device.displayed_cart_session_id,
                displayed_revision=device.displayed_revision,
            )
            if device is not None
            else None
        ),
    )


def _cart_read(cart: CartSession) -> CartSessionRead:
    return CartSessionRead(
        id=cart.id,
        status=cart.status,
        revision=cart.revision,
        pos_terminal_id=cart.pos_terminal_id,
        kiosk_device_id=cart.kiosk_device_id,
        snapshot=CartSnapshotRead.model_validate(cart.snapshot),
        changes=cart.last_changes,
        created_at=cart.created_at,
        updated_at=cart.updated_at,
    )


@kiosk_router.post(
    "/device-sessions",
    response_model=KioskDeviceSessionRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createKioskDeviceSession",
)
async def create_kiosk_device_session(
    body: KioskDeviceLoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    throttle: ThrottleDep,
) -> KioskDeviceSessionRead:
    _require_trusted_origin(request)
    ip = request.client.host if request.client is not None else "unknown"
    retry_after = throttle.retry_after(body.username, ip)
    if retry_after is not None:
        _security_log.warning(
            "kiosk login throttled username=%s ip=%s retry_after=%.0f",
            body.username,
            ip,
            retry_after,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="嘗試次數過多，請稍後再試",
            headers={"Retry-After": str(math.ceil(retry_after))},
        )
    service = CustomerDisplayService(session)
    try:
        result = await service.create_device_session(
            username=body.username,
            password=body.password,
            installation_id=body.installation_id,
            label=body.label,
        )
    except InvalidKioskCredentials as exc:
        await session.rollback()
        throttle.record_failure(body.username, ip)
        _security_log.warning("kiosk login failed username=%s ip=%s", body.username, ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    throttle.record_success(body.username, ip)
    response.set_cookie(
        KIOSK_COOKIE,
        result.raw_session_token,
        max_age=KIOSK_COOKIE_MAX_AGE,
        httponly=True,
        secure=get_settings().app_env == "production",
        samesite="strict",
        path=KIOSK_COOKIE_PATH,
    )
    payload = KioskDeviceSessionRead(
        device_id=result.device.id,
        label=result.device.label,
        csrf_token=result.raw_csrf_token,
        pairing_code=result.pairing_code,
        pairing_code_expires_at=result.pairing_code_expires_at,
        paired_terminal=_terminal_summary(result.paired_terminal),
    )
    await session.commit()
    return payload


@kiosk_router.get(
    "/cart/current",
    response_model=CartSessionRead | None,
    operation_id="getCurrentKioskCart",
)
async def get_current_kiosk_cart(
    session: SessionDep,
    principal: KioskPrincipalDep,
) -> CartSessionRead | None:
    cart = await CustomerDisplayService(session).current_cart_for_device(principal)
    return _cart_read(cart) if cart is not None else None


@kiosk_router.get(
    "/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
    operation_id="streamKioskEvents",
)
async def stream_kiosk_events(
    request: Request,
    session: SessionDep,
    principal: KioskPrincipalDep,
) -> StreamingResponse:
    """只送版本通知；客顯收到或重連後一律另 GET 完整最新狀態。"""
    _require_trusted_origin(request)

    async def events() -> AsyncIterator[str]:
        previous: tuple[int | None, int] | None = None
        heartbeat_elapsed = SSE_HEARTBEAT_SECONDS
        while not await request.is_disconnected():
            # 同一長連線持有的 identity map 不可遮蔽別筆交易剛提交的 revision。
            session.expire_all()
            cart = await CustomerDisplayService(session).current_cart_for_device(principal)
            current = (cart.id, cart.revision) if cart is not None else (None, 0)
            if current != previous:
                yield _sse_event(
                    "state",
                    {"cart_session_id": current[0], "revision": current[1]},
                    event_id=f"cart:{current[0] or 0}:{current[1]}",
                )
                previous = current
            if heartbeat_elapsed >= SSE_HEARTBEAT_SECONDS:
                yield ": heartbeat\n\n"
                heartbeat_elapsed = 0
            await asyncio.sleep(SSE_POLL_SECONDS)
            heartbeat_elapsed += SSE_POLL_SECONDS

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@kiosk_router.get(
    "/device",
    response_model=KioskDeviceRead,
    operation_id="getKioskDevice",
)
async def get_kiosk_device(
    session: SessionDep,
    principal: KioskPrincipalDep,
) -> KioskDeviceRead:
    service = CustomerDisplayService(session)
    device, terminal = await service.device_status(principal)
    return KioskDeviceRead(
        device_id=device.id,
        label=device.label,
        pairing_code=None,
        pairing_code_expires_at=None,
        paired_terminal=_terminal_summary(terminal),
    )


@kiosk_router.post(
    "/heartbeat",
    response_model=KioskHeartbeatRead,
    operation_id="recordKioskHeartbeat",
)
async def record_kiosk_heartbeat(
    body: KioskHeartbeatRequest,
    session: SessionDep,
    principal: KioskMutationDep,
) -> KioskHeartbeatRead:
    try:
        seen = await CustomerDisplayService(session).heartbeat(
            principal,
            current_session_id=body.current_session_id,
            displayed_revision=body.displayed_revision,
        )
    except CartSessionConflict as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await session.commit()
    return KioskHeartbeatRead(online=True, last_seen_at=seen)


@kiosk_router.post(
    "/pairing-codes",
    response_model=KioskDeviceRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createKioskPairingCode",
)
async def create_kiosk_pairing_code(
    session: SessionDep,
    principal: KioskMutationDep,
) -> KioskDeviceRead:
    service = CustomerDisplayService(session)
    try:
        device, code, expires_at = await service.issue_pairing_code(principal)
    except PairingConflict as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    payload = KioskDeviceRead(
        device_id=device.id,
        label=device.label,
        pairing_code=code,
        pairing_code_expires_at=expires_at,
        paired_terminal=None,
    )
    await session.commit()
    return payload


@staff_router.post(
    "/terminals",
    response_model=TerminalRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="registerPosTerminal",
)
async def register_terminal(
    body: TerminalCreateRequest,
    session: SessionDep,
    user: StaffDep,
) -> TerminalRead:
    service = CustomerDisplayService(session)
    terminal = await service.register_terminal(
        user.store_id,
        installation_id=body.installation_id,
        name=body.name,
        actor_user_id=user.id,
    )
    terminal, device = await service.terminal_read(user.store_id, terminal)
    payload = _terminal_read(terminal, device)
    await session.commit()
    return payload


@staff_router.get(
    "/terminals/{terminal_id}",
    response_model=TerminalRead,
    operation_id="getPosTerminal",
)
async def get_terminal(
    terminal_id: int,
    session: SessionDep,
    user: StaffDep,
) -> TerminalRead:
    try:
        terminal, device = await CustomerDisplayService(session).get_terminal(
            user.store_id,
            terminal_id,
        )
    except TerminalNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _terminal_read(terminal, device)


@staff_router.put(
    "/terminals/{terminal_id}/cart",
    response_model=CartSessionRead,
    operation_id="upsertCustomerDisplayCart",
)
async def upsert_cart(
    terminal_id: int,
    body: CartUpsertRequest,
    session: SessionDep,
    user: StaffDep,
) -> CartSessionRead:
    try:
        cart = await CustomerDisplayService(session).upsert_cart(
            user.store_id,
            terminal_id,
            body,
            actor_user_id=user.id,
        )
    except TerminalNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (PairingConflict, CartSessionConflict) as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (CartSessionInvalid, DomainError) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    payload = _cart_read(cart)
    await session.commit()
    return payload


@staff_router.get(
    "/terminals/{terminal_id}/cart/current",
    response_model=CartSessionRead | None,
    operation_id="getCurrentCustomerDisplayCart",
)
async def get_current_terminal_cart(
    terminal_id: int,
    session: SessionDep,
    user: StaffDep,
) -> CartSessionRead | None:
    try:
        cart = await CustomerDisplayService(session).current_cart_for_terminal(
            user.store_id,
            terminal_id,
        )
    except TerminalNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _cart_read(cart) if cart is not None else None


@staff_router.post(
    "/terminals/{terminal_id}/cart/cancel",
    response_model=CartSessionRead,
    operation_id="cancelCustomerDisplayCart",
)
async def cancel_cart(
    terminal_id: int,
    body: CartCancelRequest,
    session: SessionDep,
    user: StaffDep,
) -> CartSessionRead:
    try:
        cart = await CustomerDisplayService(session).cancel_cart(
            user.store_id,
            terminal_id,
            expected_revision=body.expected_revision,
            reason=body.reason,
            actor_user_id=user.id,
        )
    except TerminalNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CartSessionConflict as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    payload = _cart_read(cart)
    await session.commit()
    return payload


@staff_router.post(
    "/terminals/{terminal_id}/pair",
    response_model=TerminalRead,
    operation_id="pairPosTerminal",
)
async def pair_terminal(
    terminal_id: int,
    body: TerminalPairRequest,
    session: SessionDep,
    user: StaffDep,
) -> TerminalRead:
    service = CustomerDisplayService(session)
    try:
        terminal, device = await service.pair_terminal(
            user.store_id,
            terminal_id,
            pairing_code=body.pairing_code,
            actor_user_id=user.id,
        )
    except TerminalNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PairingConflict as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    payload = _terminal_read(terminal, device)
    await session.commit()
    return payload


@staff_router.post(
    "/terminals/{terminal_id}/unpair",
    response_model=TerminalRead,
    operation_id="unpairPosTerminal",
)
async def unpair_terminal(
    terminal_id: int,
    body: TerminalUnpairRequest,
    session: SessionDep,
    user: StaffDep,
) -> TerminalRead:
    service = CustomerDisplayService(session)
    try:
        terminal = await service.unpair_terminal(
            user.store_id,
            terminal_id,
            reason=body.reason,
            actor_user_id=user.id,
        )
    except TerminalNotFound as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PairingConflict as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    payload = _terminal_read(terminal, None)
    await session.commit()
    return payload
