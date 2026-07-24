"""Integration-test helpers for the mandatory paired customer-display workflow."""

import base64
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.modules.customerdisplay.models import (
    KioskDevice,
    PosTerminal,
    TerminalKioskPairing,
)
from app.modules.customerdisplay.schemas import CartUpsertRequest
from app.modules.customerdisplay.service import CartSessionInvalid, CustomerDisplayService
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.modules.user.models import User
from app.shared.enums import PayoutMethod, SignatureTaskKind, UserRole


@dataclass(frozen=True)
class SignedCartContext:
    signature_task_id: int
    cart_session_id: int
    cart_revision: int


def signature_png_base64() -> str:
    magic = b"\x89PNG\r\n\x1a\n"

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + chunk_type
            + data
            + zlib.crc32(chunk_type + data).to_bytes(4, "big")
        )

    width, height = 200, 80
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for _x in range(width):
            raw += b"\x00\x00\x00\xff" if 20 <= y <= 40 else b"\xff\xff\xff\xff"
    image = (
        magic
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(image).decode()


async def ensure_paired_customer_display(
    session: AsyncSession,
    *,
    store_id: int,
    actor_user_id: int,
) -> tuple[PosTerminal, KioskDevice]:
    pairing = await session.scalar(
        select(TerminalKioskPairing).where(
            TerminalKioskPairing.store_id == store_id,
            TerminalKioskPairing.unpaired_at.is_(None),
        )
    )
    if pairing is not None:
        terminal = await session.get(PosTerminal, pairing.pos_terminal_id)
        device = await session.get(KioskDevice, pairing.kiosk_device_id)
        assert terminal is not None and device is not None
        device.last_seen_at = datetime.now(UTC)
        await session.flush()
        return terminal, device

    kiosk_user = User(
        store_id=store_id,
        username=f"test-kiosk-{store_id}-{uuid4()}",
        password_hash="test-only",
        role=UserRole.KIOSK,
    )
    session.add(kiosk_user)
    await session.flush()
    terminal = PosTerminal(
        store_id=store_id,
        installation_id=str(uuid4()),
        name="測試櫃檯",
        created_by=actor_user_id,
        last_seen_at=datetime.now(UTC),
    )
    device = KioskDevice(
        store_id=store_id,
        kiosk_user_id=kiosk_user.id,
        installation_id=str(uuid4()),
        label="測試客顯",
        last_seen_at=datetime.now(UTC),
    )
    session.add_all([terminal, device])
    await session.flush()
    session.add(
        TerminalKioskPairing(
            store_id=store_id,
            pos_terminal_id=terminal.id,
            kiosk_device_id=device.id,
            paired_by=actor_user_id,
            paired_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return terminal, device


def token_identity(token: str) -> tuple[int, int]:
    claims = decode_access_token(token)
    return int(claims["store_id"]), int(claims["sub"])


async def signed_store_credit_sale_payload(
    session: AsyncSession,
    *,
    token: str,
    payload: dict[str, object],
) -> dict[str, object]:
    """Create, freeze, ACK and sign the authoritative cart matching a sale payload."""
    store_id, actor_user_id = token_identity(token)
    context = await prepare_signed_store_credit_cart(
        session,
        store_id=store_id,
        actor_user_id=actor_user_id,
        payload=payload,
    )
    return {
        **payload,
        "signature_task_id": context.signature_task_id,
        "cart_session_id": context.cart_session_id,
        "cart_revision": context.cart_revision,
    }


async def prepare_signed_store_credit_cart(
    session: AsyncSession,
    *,
    store_id: int,
    actor_user_id: int,
    payload: dict[str, object],
) -> SignedCartContext:
    """Create, freeze, ACK and sign an authoritative cart for service-level tests."""
    terminal, device = await ensure_paired_customer_display(
        session,
        store_id=store_id,
        actor_user_id=actor_user_id,
    )
    cart_request = CartUpsertRequest.model_validate(
        {
            "expected_revision": None,
            "lines": payload["lines"],
            "buyer_contact_id": payload.get("buyer_contact_id"),
            "tenders": payload.get("tenders"),
        }
    )
    display = CustomerDisplayService(session)
    cart = await display.upsert_cart(
        store_id,
        terminal.id,
        cart_request,
        actor_user_id=actor_user_id,
    )
    cart, task = await display.freeze_store_credit_cart(
        store_id,
        terminal.id,
        expected_revision=cart.revision,
        actor_user_id=actor_user_id,
    )
    signing = SigningService(session)
    await signing.acknowledge_task(store_id, device.id, task.id)
    task = await signing.sign_task(
        store_id,
        task.id,
        device_id=device.id,
        signature_image_base64=signature_png_base64(),
        chosen_payout=None,
    )
    await session.commit()
    return SignedCartContext(
        signature_task_id=task.id,
        cart_session_id=cart.id,
        cart_revision=cart.revision,
    )


async def signed_affidavit(
    session: AsyncSession,
    *,
    store_id: int,
    contact_id: int,
    actor_user_id: int,
    content: dict[str, object],
    payout: PayoutMethod,
) -> int:
    terminal, device = await ensure_paired_customer_display(
        session,
        store_id=store_id,
        actor_user_id=actor_user_id,
    )
    signing = SigningService(session)
    task = await signing.create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
            contact_id=contact_id,
            content=content,
            terminal_id=terminal.id,
        ),
        created_by=actor_user_id,
    )
    await signing.acknowledge_task(store_id, device.id, task.id)
    task = await signing.sign_task(
        store_id,
        task.id,
        device_id=device.id,
        signature_image_base64=signature_png_base64(),
        chosen_payout=payout,
    )
    await session.commit()
    return task.id


class CustomerDisplayAwareClient(httpx.AsyncClient):
    """Keeps legacy domain tests focused while satisfying mandatory store-credit signing."""

    def __init__(self, *args: object, db_session: AsyncSession, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._db_session = db_session

    async def post(  # type: ignore[override]
        self, url: str, *args: object, **kwargs: object
    ) -> httpx.Response:
        body = kwargs.get("json")
        headers = kwargs.get("headers")
        if (
            url == "/api/v1/sales"
            and isinstance(body, dict)
            and body.get("signature_task_id") is None
            and isinstance(body.get("tenders"), list)
            and len(body["tenders"]) <= 2
            and body.get("buyer_contact_id") is not None
            and any(
                isinstance(tender, dict) and tender.get("tender_type") == "STORE_CREDIT"
                for tender in body["tenders"]
            )
            and isinstance(headers, dict)
        ):
            authorization = next(
                (
                    str(value)
                    for key, value in headers.items()
                    if str(key).lower() == "authorization"
                ),
                "",
            )
            if authorization.startswith("Bearer "):
                try:
                    body = await signed_store_credit_sale_payload(
                        self._db_session,
                        token=authorization.removeprefix("Bearer "),
                        payload=body,
                    )
                    kwargs["json"] = body
                except CartSessionInvalid:
                    pass
        return await super().post(url, *args, **kwargs)  # type: ignore[arg-type]


async def delete_customer_display_rows(session: AsyncSession, *, store_id: int) -> None:
    """Remove test-only display evidence before legacy raw-FK cleanup blocks."""
    await session.execute(text("SET session_replication_role = replica"))
    try:
        await session.execute(
            text(
                "UPDATE cart_sessions SET active_signature_task_id=NULL, sale_id=NULL "
                "WHERE store_id=:store_id"
            ),
            {"store_id": store_id},
        )
        await session.execute(
            text("UPDATE signature_tasks SET cart_session_id=NULL WHERE store_id=:store_id"),
            {"store_id": store_id},
        )
        await session.execute(
            text("UPDATE sales SET signature_task_id=NULL WHERE store_id=:store_id"),
            {"store_id": store_id},
        )
        await session.execute(
            text(
                "UPDATE kiosk_devices SET displayed_cart_session_id=NULL WHERE store_id=:store_id"
            ),
            {"store_id": store_id},
        )
        for table in (
            "cart_session_events",
            "signature_task_events",
            "cart_sessions",
            "signature_tasks",
            "kiosk_pairing_codes",
            "kiosk_device_sessions",
            "terminal_kiosk_pairings",
            "kiosk_devices",
            "pos_terminals",
        ):
            await session.execute(
                text(f"DELETE FROM {table} WHERE store_id = :store_id"),
                {"store_id": store_id},
            )
    finally:
        await session.execute(text("SET session_replication_role = origin"))
