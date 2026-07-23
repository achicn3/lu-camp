"""客顯裝置 cookie session 與店務端櫃檯配對路由。"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import CurrentUser, get_current_user
from app.modules.customerdisplay.models import KioskDevice, PosTerminal
from app.modules.customerdisplay.schemas import (
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
    CustomerDisplayService,
    DevicePrincipal,
    InvalidCsrfToken,
    InvalidDeviceSession,
    InvalidKioskCredentials,
    PairingConflict,
    TerminalNotFound,
)

KIOSK_COOKIE = "lu_camp_kiosk_session"
KIOSK_COOKIE_PATH = "/api/v1/kiosk"
KIOSK_COOKIE_MAX_AGE = 365 * 24 * 60 * 60

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
            KioskSummary(id=device.id, label=device.label) if device is not None else None
        ),
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
) -> KioskDeviceSessionRead:
    _require_trusted_origin(request)
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
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
    del body
    seen = await CustomerDisplayService(session).heartbeat(principal)
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
