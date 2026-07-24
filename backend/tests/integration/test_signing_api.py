"""Customer-display signing API integration tests for the paired-device lifecycle."""

import base64
import zlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_pii_cipher, national_id_blind_index
from app.core.db import get_session
from app.core.security import encode_access_token, hash_password
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.signing.models import AgreementVersion, SignatureTask, SignatureTaskEvent
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import SignatureTaskStatus, UserRole

ORIGIN = "http://localhost:3000"


def _signature_png(width: int = 200, height: int = 80) -> str:
    magic = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big") + kind + data + zlib.crc32(kind + data).to_bytes(4, "big")
        )

    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for _x in range(width):
            raw += b"\x00\x00\x00\xff" if 20 <= y <= 40 else b"\xff\xff\xff\xff"
    png = (
        magic
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


PNG_B64 = _signature_png()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def override_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Origin": ORIGIN},
    ) as api_client:
        yield api_client
    app.dependency_overrides.clear()


@dataclass
class Seeded:
    store_id: int
    manager_id: int
    clerk_id: int
    contact_id: int
    manager_token: str
    clerk_token: str
    kiosk_token: str
    kiosk_username: str
    terminal_id: int | None = None
    kiosk_device_id: int | None = None
    csrf_token: str | None = None


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _csrf(seeded: Seeded) -> dict[str, str]:
    assert seeded.csrf_token is not None
    return {"X-CSRF-Token": seeded.csrf_token}


async def _seed(session: AsyncSession, *, suffix: str = "") -> Seeded:
    store = Store(name=f"簽署測試門市{suffix}")
    session.add(store)
    await session.flush()
    manager = User(
        store_id=store.id,
        username=f"sign-manager-{store.id}",
        password_hash=hash_password("manager-secret"),
        role=UserRole.MANAGER,
    )
    clerk = User(
        store_id=store.id,
        username=f"sign-clerk-{store.id}",
        password_hash=hash_password("clerk-secret"),
        role=UserRole.CLERK,
    )
    kiosk = User(
        store_id=store.id,
        username=f"sign-kiosk-{store.id}",
        password_hash=hash_password("kiosk-secret"),
        role=UserRole.KIOSK,
    )
    contact = Contact(
        store_id=store.id,
        name="王小明",
        phone="0912345678",
        national_id_enc=get_pii_cipher().encrypt("A123456789"),
        national_id_blind_index=national_id_blind_index("A123456789"),
        roles=["SELLER", "MEMBER"],
    )
    session.add_all([manager, clerk, kiosk, contact])
    await session.flush()
    await session.commit()
    return Seeded(
        store_id=store.id,
        manager_id=manager.id,
        clerk_id=clerk.id,
        contact_id=contact.id,
        manager_token=encode_access_token(
            user_id=manager.id, role=manager.role.value, store_id=store.id
        ),
        clerk_token=encode_access_token(user_id=clerk.id, role=clerk.role.value, store_id=store.id),
        kiosk_token=encode_access_token(user_id=kiosk.id, role=kiosk.role.value, store_id=store.id),
        kiosk_username=kiosk.username,
    )


async def _pair(client: httpx.AsyncClient, seeded: Seeded) -> Seeded:
    login = await client.post(
        "/api/v1/kiosk/device-sessions",
        json={
            "username": seeded.kiosk_username,
            "password": "kiosk-secret",
            "installation_id": f"00000000-0000-4000-8000-{seeded.store_id:012d}",
            "label": f"客顯 {seeded.store_id}",
        },
    )
    assert login.status_code == 201, login.text
    kiosk = login.json()
    terminal = await client.post(
        "/api/v1/customer-display/terminals",
        headers=_auth(seeded.manager_token),
        json={
            "installation_id": f"10000000-0000-4000-8000-{seeded.store_id:012d}",
            "name": f"櫃檯 {seeded.store_id}",
        },
    )
    assert terminal.status_code == 201, terminal.text
    paired = await client.post(
        f"/api/v1/customer-display/terminals/{terminal.json()['id']}/pair",
        headers=_auth(seeded.manager_token),
        json={"pairing_code": kiosk["pairing_code"]},
    )
    assert paired.status_code == 200, paired.text
    seeded.terminal_id = int(terminal.json()["id"])
    seeded.kiosk_device_id = int(kiosk["device_id"])
    seeded.csrf_token = str(kiosk["csrf_token"])
    return seeded


async def _prepare(client: httpx.AsyncClient, session: AsyncSession, *, suffix: str = "") -> Seeded:
    return await _pair(client, await _seed(session, suffix=suffix))


def _task_payload(seeded: Seeded, **overrides: object) -> dict[str, object]:
    assert seeded.terminal_id is not None
    payload: dict[str, object] = {
        "kind": "ACQUISITION_AFFIDAVIT",
        "contact_id": seeded.contact_id,
        "terminal_id": seeded.terminal_id,
        "content": {
            "items": [{"name": "登山背包", "amount": "1200"}],
            "total": "1200",
            "seller_name": "偽造姓名會由後端覆蓋",
        },
    }
    payload.update(overrides)
    return payload


async def _create_task(
    client: httpx.AsyncClient,
    seeded: Seeded,
    **overrides: object,
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/signing/tasks",
        headers=_auth(seeded.clerk_token),
        json=_task_payload(seeded, **overrides),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert isinstance(body, dict)
    return cast(dict[str, Any], body)


async def _ack(client: httpx.AsyncClient, seeded: Seeded, task_id: int) -> httpx.Response:
    return await client.post(
        f"/api/v1/kiosk/tasks/{task_id}/ack",
        headers=_csrf(seeded),
    )


async def _sign(
    client: httpx.AsyncClient,
    seeded: Seeded,
    task_id: int,
    *,
    payout: str | None = "CASH",
    image: str = PNG_B64,
    idempotency_key: str | None = None,
) -> httpx.Response:
    body: dict[str, object] = {
        "signature_image_base64": image,
        "chosen_payout": payout,
    }
    if idempotency_key is not None:
        body["idempotency_key"] = idempotency_key
    return await client.post(
        f"/api/v1/kiosk/tasks/{task_id}/sign",
        headers=_csrf(seeded),
        json=body,
    )


async def test_kiosk_bearer_is_blocked_from_staff_endpoints(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _seed(db_session)
    response = await client.get("/api/v1/contacts?limit=10", headers=_auth(seeded.kiosk_token))
    assert response.status_code == 403


async def test_staff_bearer_cannot_replace_kiosk_device_cookie(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _seed(db_session)
    response = await client.get(
        "/api/v1/kiosk/tasks/current",
        headers=_auth(seeded.manager_token),
    )
    assert response.status_code == 401


async def test_affidavit_requires_pairing_and_binds_agreement(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _seed(db_session)
    unpaired = await client.post(
        "/api/v1/signing/tasks",
        headers=_auth(seeded.clerk_token),
        json={
            "kind": "ACQUISITION_AFFIDAVIT",
            "contact_id": seeded.contact_id,
            "content": {"items": [{"name": "背包", "amount": "1200"}], "total": "1200"},
        },
    )
    assert unpaired.status_code == 409

    await _pair(client, seeded)
    task = await _create_task(client, seeded)
    assert task["status"] == "PENDING"
    assert task["agreement_version"] == 1
    assert task["content"]["seller_name"] == "王小明"
    assert await db_session.scalar(select(func.count()).select_from(AgreementVersion)) == 1


async def test_explicit_void_is_required_before_repush(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _prepare(client, db_session)
    first = await _create_task(client, seeded)
    conflict = await client.post(
        "/api/v1/signing/tasks",
        headers=_auth(seeded.clerk_token),
        json=_task_payload(seeded),
    )
    assert conflict.status_code == 409

    voided = await client.post(
        f"/api/v1/signing/tasks/{first['id']}/cancel",
        headers=_auth(seeded.clerk_token),
    )
    assert voided.status_code == 200
    assert voided.json()["status"] == "VOIDED"
    assert voided.json()["voided_at"] is not None
    second = await _create_task(client, seeded)
    assert second["id"] != first["id"]


async def test_ack_sign_and_append_only_evidence_chain(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _prepare(client, db_session)
    task = await _create_task(client, seeded)
    task_id = int(task["id"])

    before_ack = await _sign(client, seeded, task_id)
    assert before_ack.status_code == 409
    acknowledged = await _ack(client, seeded, task_id)
    assert acknowledged.status_code == 200
    assert acknowledged.json()["status"] == "SIGNING"
    assert {
        "store_id",
        "contact_id",
        "ref_type",
        "ref_id",
        "created_at",
        "bound_acquisition_id",
        "bound_sale_id",
        "signer_name",
    }.isdisjoint(acknowledged.json())

    activity = await client.post(
        f"/api/v1/kiosk/tasks/{task_id}/activity",
        headers=_csrf(seeded),
        json={"activity": "SIGNATURE_STARTED"},
    )
    assert activity.status_code == 200
    signed = await _sign(
        client,
        seeded,
        task_id,
        idempotency_key="signature-attempt-1",
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["status"] == "SIGNED"
    assert signed.json()["content"] == {}
    assert signed.json()["agreement_body"] is None

    stored = await db_session.get(SignatureTask, task_id)
    assert stored is not None
    assert stored.signature_sha256 and len(stored.signature_sha256) == 64
    assert stored.content_sha256 and len(stored.content_sha256) == 64
    assert stored.evidence_hash and len(stored.evidence_hash) == 64
    assert stored.signature_retention_until is not None
    assert stored.retention_policy == "TRANSACTION_RECORD_5Y"
    statuses = list(
        (
            await db_session.scalars(
                select(SignatureTaskEvent.to_status)
                .where(SignatureTaskEvent.signature_task_id == task_id)
                .order_by(SignatureTaskEvent.id)
            )
        ).all()
    )
    assert statuses[:2] == [SignatureTaskStatus.PENDING, SignatureTaskStatus.SIGNING]
    assert statuses[-1] is SignatureTaskStatus.SIGNED


async def test_signed_task_survives_reconnect_until_staff_decides(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _prepare(client, db_session)
    task = await _create_task(client, seeded)
    task_id = int(task["id"])
    assert (await _ack(client, seeded, task_id)).status_code == 200
    assert (await _sign(client, seeded, task_id)).status_code == 200

    current = await client.get("/api/v1/kiosk/tasks/current")
    assert current.status_code == 200
    assert current.json()["id"] == task_id
    assert current.json()["status"] == "SIGNED"
    assert current.json()["content"] == {}

    voided = await client.post(
        f"/api/v1/signing/tasks/{task_id}/cancel",
        headers=_auth(seeded.clerk_token),
    )
    assert voided.status_code == 200
    assert (await client.get("/api/v1/kiosk/tasks/current")).json() is None
    assert (await client.get(f"/api/v1/kiosk/tasks/{task_id}")).status_code == 404


async def test_direct_kiosk_task_read_lazily_expires_stale_task(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _prepare(client, db_session)
    task_id = int((await _create_task(client, seeded))["id"])
    task = await db_session.get(SignatureTask, task_id)
    assert task is not None
    task.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    response = await client.get(f"/api/v1/kiosk/tasks/{task_id}")

    assert response.status_code == 404
    db_session.expire_all()
    expired = await db_session.get(SignatureTask, task_id)
    assert expired is not None
    assert expired.status is SignatureTaskStatus.EXPIRED


async def test_sign_is_idempotent_only_for_same_key_and_evidence(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _prepare(client, db_session)
    task_id = int((await _create_task(client, seeded))["id"])
    assert (await _ack(client, seeded, task_id)).status_code == 200
    first = await _sign(client, seeded, task_id, idempotency_key="same-attempt")
    replay = await _sign(client, seeded, task_id, idempotency_key="same-attempt")
    changed = await _sign(
        client,
        seeded,
        task_id,
        image=_signature_png(220, 90),
        idempotency_key="same-attempt",
    )
    assert first.status_code == replay.status_code == 200
    assert changed.status_code == 409


async def test_kiosk_mutations_require_origin_and_csrf(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _prepare(client, db_session)
    task_id = int((await _create_task(client, seeded))["id"])
    missing_csrf = await client.post(f"/api/v1/kiosk/tasks/{task_id}/ack")
    assert missing_csrf.status_code == 403
    wrong_origin = await client.post(
        f"/api/v1/kiosk/tasks/{task_id}/ack",
        headers={"Origin": "https://evil.example", **_csrf(seeded)},
    )
    assert wrong_origin.status_code == 403
    assert (await _ack(client, seeded, task_id)).status_code == 200


async def test_store_credit_task_cannot_bypass_authoritative_cart(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _prepare(client, db_session)
    response = await client.post(
        "/api/v1/signing/tasks",
        headers=_auth(seeded.clerk_token),
        json=_task_payload(
            seeded,
            kind="STORE_CREDIT_USE",
            content={"debit": "300", "sale_total": "300"},
        ),
    )
    assert response.status_code == 409
    assert "權威購物車" in response.text


async def test_affidavit_payout_is_binary(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _prepare(client, db_session)
    task_id = int((await _create_task(client, seeded))["id"])
    assert (await _ack(client, seeded, task_id)).status_code == 200
    for payout in (None, "SPLIT"):
        response = await _sign(client, seeded, task_id, payout=payout)
        assert response.status_code == 422
    assert (await _sign(client, seeded, task_id, payout="STORE_CREDIT")).status_code == 200


async def test_invalid_and_blank_png_are_rejected_without_losing_signing_state(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _prepare(client, db_session)
    task_id = int((await _create_task(client, seeded))["id"])
    assert (await _ack(client, seeded, task_id)).status_code == 200

    magic = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big") + kind + data + zlib.crc32(kind + data).to_bytes(4, "big")
        )

    width, height = 200, 80
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    blank_raw = b"".join(b"\x00" + b"\xff\xff\xff\xff" * width for _ in range(height))
    blank = base64.b64encode(
        magic
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(blank_raw))
        + chunk(b"IEND", b"")
    ).decode()
    invalid = [
        "not-base64!!!",
        base64.b64encode(b"GIF89a").decode(),
        base64.b64encode(magic + b"broken").decode(),
        blank,
    ]
    for image in invalid:
        response = await _sign(client, seeded, task_id, image=image)
        assert response.status_code == 422, response.text
    detail = await client.get(
        f"/api/v1/signing/tasks/{task_id}",
        headers=_auth(seeded.clerk_token),
    )
    assert detail.json()["status"] == "SIGNING"


async def test_signature_png_is_normalized_and_retrievable(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _prepare(client, db_session)
    task_id = int((await _create_task(client, seeded))["id"])
    assert (await _ack(client, seeded, task_id)).status_code == 200
    assert (await _sign(client, seeded, task_id)).status_code == 200
    image = await client.get(
        f"/api/v1/signing/tasks/{task_id}/signature",
        headers=_auth(seeded.clerk_token),
    )
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")


async def test_kiosk_body_size_guard_runs_before_json_parsing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _prepare(client, db_session)
    task_id = int((await _create_task(client, seeded))["id"])
    response = await client.post(
        f"/api/v1/kiosk/tasks/{task_id}/sign",
        headers=_csrf(seeded),
        json={"signature_image_base64": "A" * 1_100_000, "chosen_payout": "CASH"},
    )
    assert response.status_code == 413


async def test_cross_store_device_cannot_read_or_sign_task(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    first = await _prepare(client, db_session, suffix="-A")
    task_id = int((await _create_task(client, first))["id"])
    second = await _prepare(client, db_session, suffix="-B")
    assert (await client.get(f"/api/v1/kiosk/tasks/{task_id}")).status_code == 404
    assert (await _ack(client, second, task_id)).status_code == 404


async def test_list_filters_and_staff_history_remain_available(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await _prepare(client, db_session)
    task = await _create_task(client, seeded)
    await client.post(
        f"/api/v1/signing/tasks/{task['id']}/cancel",
        headers=_auth(seeded.clerk_token),
    )
    listed = await client.get(
        "/api/v1/signing/tasks",
        params={
            "kind": "ACQUISITION_AFFIDAVIT",
            "contact_id": seeded.contact_id,
            "status": "VOIDED",
        },
        headers=_auth(seeded.clerk_token),
    )
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [task["id"]]
