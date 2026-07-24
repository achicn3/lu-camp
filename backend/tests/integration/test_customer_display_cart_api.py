"""客顯即時購物車 session API。

公開行為守護：
- 第一件商品建立 DRAFT；價格、折扣與總額一律由 sales quote 在後端計算。
- 會員 payload 只有遮罩姓名，不含電話、證號或可逆 PII。
- revision 採 optimistic concurrency；舊 revision 不得覆蓋新資料，回應遺失的同內容重送可回放。
- POS 與配對客顯可各自恢復同一未完成 session；清空只從 DRAFT 轉 CANCELLED。
"""

import base64
import zlib
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token, hash_password
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.customerdisplay.models import CartSession, CartSessionEvent, KioskDevice
from app.modules.customerdisplay.service import CustomerDisplayService
from app.modules.inventory.models import CatalogProduct
from app.modules.sales.linepay import LinePayClient, LinePayTransport
from app.modules.sales.models import LinePayTransaction, Sale
from app.modules.settings.schemas import SettingsUpdateRequest
from app.modules.settings.service import StoreSettingsService
from app.modules.signing.models import SignatureTask, SignatureTaskEvent
from app.modules.signing.service import SigningService
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import UserRole
from app.shared.exceptions import LinePayTransportError

ORIGIN = "http://localhost:3000"


def _signature_png() -> str:
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
    png = (
        magic
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Origin": ORIGIN},
    ) as c:
        yield c
    app.dependency_overrides.clear()


class Seeded:
    def __init__(
        self,
        *,
        manager_token: str,
        kiosk_username: str,
        kiosk_password: str,
        member_id: int,
        product_id: int,
    ) -> None:
        self.manager_token = manager_token
        self.kiosk_username = kiosk_username
        self.kiosk_password = kiosk_password
        self.member_id = member_id
        self.product_id = product_id


async def _seed(session: AsyncSession, suffix: str) -> Seeded:
    store = Store(name=f"購物車測試店-{suffix}")
    session.add(store)
    await session.flush()
    manager = User(
        store_id=store.id,
        username=f"cart-manager-{suffix}",
        password_hash=hash_password("manager-secret"),
        role=UserRole.MANAGER,
    )
    kiosk = User(
        store_id=store.id,
        username=f"cart-kiosk-{suffix}",
        password_hash=hash_password("kiosk-secret"),
        role=UserRole.KIOSK,
    )
    member = Contact(
        store_id=store.id,
        name="王小明",
        phone=f"0912{store.id:06d}"[-10:],
        roles=["MEMBER"],
    )
    product = CatalogProduct(
        store_id=store.id,
        sku=f"CART-{suffix}",
        name="營釘補充包",
        unit_price=Decimal("120"),
        quantity_on_hand=20,
        reorder_point=2,
    )
    session.add_all([manager, kiosk, member, product])
    await session.flush()
    await session.commit()
    return Seeded(
        manager_token=encode_access_token(
            user_id=manager.id,
            role=manager.role.value,
            store_id=store.id,
        ),
        kiosk_username=kiosk.username,
        kiosk_password="kiosk-secret",
        member_id=member.id,
        product_id=product.id,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _pair(
    client: httpx.AsyncClient,
    seeded: Seeded,
    *,
    suffix: str,
) -> tuple[int, int, str]:
    kiosk_login = await client.post(
        "/api/v1/kiosk/device-sessions",
        json={
            "username": seeded.kiosk_username,
            "password": seeded.kiosk_password,
            "installation_id": f"00000000-0000-4000-8000-{int(suffix):012d}",
            "label": f"顧客平板 {suffix}",
        },
    )
    assert kiosk_login.status_code == 201, kiosk_login.text
    kiosk = kiosk_login.json()
    terminal_response = await client.post(
        "/api/v1/customer-display/terminals",
        headers=_auth(seeded.manager_token),
        json={
            "installation_id": f"10000000-0000-4000-8000-{int(suffix):012d}",
            "name": f"櫃檯 {suffix}",
        },
    )
    assert terminal_response.status_code == 201, terminal_response.text
    terminal_id = terminal_response.json()["id"]
    paired = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/pair",
        headers=_auth(seeded.manager_token),
        json={"pairing_code": kiosk["pairing_code"]},
    )
    assert paired.status_code == 200, paired.text
    return terminal_id, kiosk["device_id"], str(kiosk["csrf_token"])


def _cart_payload(
    seeded: Seeded,
    *,
    qty: int,
    expected_revision: int | None,
) -> dict[str, object]:
    total = 120 * qty
    return {
        "expected_revision": expected_revision,
        "lines": [
            {
                "line_type": "CATALOG",
                "catalog_product_id": seeded.product_id,
                "qty": qty,
            }
        ],
        "buyer_contact_id": seeded.member_id,
        "tenders": [{"tender_type": "CASH", "amount": str(total)}],
    }


async def test_first_item_creates_server_priced_cart_visible_only_to_paired_kiosk(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, "1")
    terminal_id, kiosk_device_id, _ = await _pair(client, seeded, suffix="1")

    response = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=_cart_payload(seeded, qty=2, expected_revision=None),
    )

    assert response.status_code == 200, response.text
    cart = response.json()
    assert cart["status"] == "DRAFT"
    assert cart["revision"] == 1
    assert cart["pos_terminal_id"] == terminal_id
    assert cart["kiosk_device_id"] == kiosk_device_id
    assert cart["snapshot"]["total"] == "240"
    assert cart["snapshot"]["discount_total"] == "0"
    assert cart["snapshot"]["items"] == [
        {
            "item_key": f"CATALOG:{seeded.product_id}",
            "line_type": "CATALOG",
            "name": "營釘補充包",
            "qty": 2,
            "unit_price": "120",
            "original_unit_price": None,
            "discount_amount": "0",
            "line_total": "240",
        }
    ]
    assert cart["snapshot"]["member"] == {"display_name": "王○明"}
    assert cart["snapshot"]["tenders"] == [{"tender_type": "CASH", "amount": "240"}]
    serialized = response.text
    assert "0912" not in serialized
    assert "national_id" not in serialized

    kiosk_read = await client.get("/api/v1/kiosk/cart/current")
    assert kiosk_read.status_code == 200, kiosk_read.text
    assert kiosk_read.json() == {
        key: value
        for key, value in cart.items()
        if key not in {"pos_terminal_id", "kiosk_device_id", "created_at"}
    }
    assert {
        "buyer_contact_id",
        "pos_terminal_id",
        "kiosk_device_id",
        "created_at",
    }.isdisjoint(kiosk_read.json())

    staff_restore = await client.get(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/current",
        headers=_auth(seeded.manager_token),
    )
    assert staff_restore.status_code == 200
    assert staff_restore.json()["buyer_contact_id"] == seeded.member_id
    assert {
        key: value
        for key, value in staff_restore.json().items()
        if key
        not in {
            "buyer_contact_id",
            "payment_order_id",
            "payment_uncertain_at",
            "payment_uncertain_reason",
            "active_signature_task_id",
            "sale_id",
        }
    } == cart
    assert staff_restore.json()["active_signature_task_id"] is None


async def test_begin_checkout_commits_observable_processing_and_blocks_mutation(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, "30")
    terminal_id, _, _ = await _pair(client, seeded, suffix="30")
    cart_payload = _cart_payload(seeded, qty=1, expected_revision=None)
    cart_payload["tenders"] = [{"tender_type": "TAIWAN_PAY", "amount": "120"}]
    created = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=cart_payload,
    )
    assert created.status_code == 200, created.text

    processing = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/begin-checkout",
        headers=_auth(seeded.manager_token),
        json={
            "expected_revision": created.json()["revision"],
            "signature_task_id": None,
        },
    )

    assert processing.status_code == 200, processing.text
    assert processing.json()["status"] == "PROCESSING"
    assert processing.json()["revision"] == created.json()["revision"] + 1
    kiosk = await client.get("/api/v1/kiosk/cart/current")
    assert kiosk.status_code == 200
    assert kiosk.json()["status"] == "PROCESSING"
    mutation = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=_cart_payload(
            seeded,
            qty=2,
            expected_revision=processing.json()["revision"],
        ),
    )
    assert mutation.status_code == 409
    failed = await client.post(
        "/api/v1/sales",
        headers={
            **_auth(seeded.manager_token),
            "Idempotency-Key": "observable-processing-mismatch",
        },
        json={
            "lines": [
                {
                    "line_type": "CATALOG",
                    "catalog_product_id": seeded.product_id,
                    "qty": 2,
                }
            ],
            "buyer_contact_id": seeded.member_id,
            "tenders": [{"tender_type": "TAIWAN_PAY", "amount": "240"}],
            "cart_session_id": processing.json()["id"],
            "cart_revision": processing.json()["revision"],
            "expected_einvoice_enabled": False,
        },
    )
    assert failed.status_code == 422
    released = await client.get(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/current",
        headers=_auth(seeded.manager_token),
    )
    assert released.status_code == 200
    assert released.json()["status"] == "DRAFT"
    restarted = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/begin-checkout",
        headers=_auth(seeded.manager_token),
        json={
            "expected_revision": released.json()["revision"],
            "signature_task_id": None,
        },
    )
    assert restarted.status_code == 200
    sale = await client.post(
        "/api/v1/sales",
        headers={
            **_auth(seeded.manager_token),
            "Idempotency-Key": "observable-processing-checkout",
        },
        json={
            "lines": [
                {
                    "line_type": "CATALOG",
                    "catalog_product_id": seeded.product_id,
                    "qty": 1,
                }
            ],
            "buyer_contact_id": seeded.member_id,
            "tenders": [{"tender_type": "TAIWAN_PAY", "amount": "120"}],
            "cart_session_id": restarted.json()["id"],
            "cart_revision": restarted.json()["revision"],
            "expected_einvoice_enabled": False,
        },
    )
    assert sale.status_code == 201, sale.text
    completed = await client.get("/api/v1/kiosk/cart/current")
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"


async def test_store_credit_freeze_ack_and_void_thaw_are_authoritative(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, "11")
    terminal_id, kiosk_device_id, csrf = await _pair(client, seeded, suffix="11")
    manager = await db_session.scalar(select(User).where(User.username == "cart-manager-11"))
    assert manager is not None
    await StoreCreditService(db_session).adjust(
        manager.store_id,
        seeded.member_id,
        amount=Decimal("100"),
        reason="簽署流程測試",
        created_by=manager.id,
        idempotency_key="freeze-test-credit",
    )
    await db_session.commit()
    cart_payload = _cart_payload(seeded, qty=1, expected_revision=None)
    cart_payload["tenders"] = [
        {"tender_type": "STORE_CREDIT", "amount": "50"},
        {"tender_type": "LINE_PAY", "amount": "70"},
    ]
    created = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=cart_payload,
    )
    assert created.status_code == 200, created.text

    frozen = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/freeze-for-signature",
        headers=_auth(seeded.manager_token),
        json={"expected_revision": created.json()["revision"]},
    )
    assert frozen.status_code == 200, frozen.text
    body = frozen.json()
    assert body["cart"]["status"] == "FROZEN"
    assert body["signature_status"] == "PENDING"
    task_id = body["signature_task_id"]
    assert body["cart"]["active_signature_task_id"] == task_id
    task = await db_session.get(SignatureTask, task_id)
    assert task is not None
    assert task.kiosk_device_id == kiosk_device_id
    assert task.content["store_credit_balance_before"] == "100"
    assert task.content["store_credit_balance_after"] == "50"
    assert task.content["remaining_tenders"] == [{"tender_type": "LINE_PAY", "amount": "70"}]
    assert "phone" not in str(task.content).lower()

    mutation = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=_cart_payload(seeded, qty=2, expected_revision=body["cart"]["revision"]),
    )
    assert mutation.status_code == 409

    current = await client.get("/api/v1/kiosk/tasks/current")
    assert current.status_code == 200, current.text
    assert current.json()["status"] == "PENDING"
    ack = await client.post(
        f"/api/v1/kiosk/tasks/{task_id}/ack",
        headers={"X-CSRF-Token": csrf},
    )
    assert ack.status_code == 200, ack.text
    assert ack.json()["status"] == "SIGNING"

    voided = await client.post(
        f"/api/v1/signing/tasks/{task_id}/cancel",
        headers=_auth(seeded.manager_token),
        json={
            "reason_code": "KIOSK_FAILURE_CASH_FALLBACK",
            "reason": "客顯故障，改用現金",
        },
    )
    assert voided.status_code == 200, voided.text
    assert voided.json()["status"] == "VOIDED"
    restored = await client.get(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/current",
        headers=_auth(seeded.manager_token),
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "DRAFT"
    refreshed_task = await db_session.get(SignatureTask, task_id)
    assert refreshed_task is not None
    assert refreshed_task.status.value == "VOIDED"
    transitions = list(
        (
            await db_session.scalars(
                select(SignatureTaskEvent)
                .where(SignatureTaskEvent.signature_task_id == task_id)
                .order_by(SignatureTaskEvent.id)
            )
        ).all()
    )
    assert [(row.from_status, row.to_status) for row in transitions] == [
        (None, "PENDING"),
        ("PENDING", "SIGNING"),
        ("SIGNING", "VOIDED"),
    ]
    assert transitions[-1].reason_code == "KIOSK_FAILURE_CASH_FALLBACK"
    assert transitions[-1].reason_detail == "客顯故障，改用現金"


async def test_signed_task_hashes_report_only_and_png_only_cleanup_boundary(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, "12")
    terminal_id, _, csrf = await _pair(client, seeded, suffix="12")
    manager = await db_session.scalar(select(User).where(User.username == "cart-manager-12"))
    assert manager is not None
    await StoreCreditService(db_session).adjust(
        manager.store_id,
        seeded.member_id,
        amount=Decimal("200"),
        reason="簽名保存測試",
        created_by=manager.id,
        idempotency_key="retention-test-credit",
    )
    await db_session.commit()
    cart_payload = _cart_payload(seeded, qty=1, expected_revision=None)
    cart_payload["tenders"] = [
        {"tender_type": "STORE_CREDIT", "amount": "50"},
        {"tender_type": "TAIWAN_PAY", "amount": "70"},
    ]
    created = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=cart_payload,
    )
    frozen = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/freeze-for-signature",
        headers=_auth(seeded.manager_token),
        json={"expected_revision": created.json()["revision"]},
    )
    task_id = frozen.json()["signature_task_id"]
    ack = await client.post(
        f"/api/v1/kiosk/tasks/{task_id}/ack",
        headers={"X-CSRF-Token": csrf},
    )
    assert ack.status_code == 200
    signed = await client.post(
        f"/api/v1/kiosk/tasks/{task_id}/sign",
        headers={"X-CSRF-Token": csrf},
        json={
            "signature_image_base64": _signature_png(),
            "idempotency_key": "retention-sign",
        },
    )
    assert signed.status_code == 200, signed.text
    assert signed.json()["status"] == "SIGNED"
    task = await db_session.get(SignatureTask, task_id)
    assert task is not None
    assert len(task.signature_sha256 or "") == 64
    assert len(task.content_sha256 or "") == 64
    assert len(task.evidence_hash or "") == 64
    assert task.signed_at is not None
    assert task.signature_retention_until is not None
    assert task.signature_retention_until - task.signed_at == timedelta(days=183)
    evidence_png = task.signature_image

    reported = await SigningService(db_session).report_due_signature_images(
        now=task.signature_retention_until + timedelta(seconds=1)
    )
    assert reported == 1
    assert task.signature_image == evidence_png
    assert task.signature_cleanup_reported_at is not None

    task.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    expired = await SigningService(db_session).sweep_expired_tasks()
    assert expired == 1
    assert task.status.value == "EXPIRED"
    assert task.signature_image == evidence_png
    cart = await db_session.get(CartSession, task.cart_session_id)
    assert cart is not None
    assert cart.status.value == "DRAFT"
    assert cart.active_signature_task_id is None

    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(SignatureTask)
                .where(SignatureTask.id == task_id)
                .values(content={"tampered": True})
            )
            await db_session.flush()

    hashes = (task.signature_sha256, task.content_sha256, task.evidence_hash)
    task.signature_image = None
    await db_session.flush()
    assert task.signature_image is None
    assert (task.signature_sha256, task.content_sha256, task.evidence_hash) == hashes


async def test_store_credit_taiwan_pay_checkout_consumes_signature_and_completes_cart(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, "13")
    terminal_id, _, csrf = await _pair(client, seeded, suffix="13")
    manager = await db_session.scalar(select(User).where(User.username == "cart-manager-13"))
    assert manager is not None
    await StoreCreditService(db_session).adjust(
        manager.store_id,
        seeded.member_id,
        amount=Decimal("200"),
        reason="成交綁定測試",
        created_by=manager.id,
        idempotency_key="checkout-test-credit",
    )
    await db_session.commit()
    cart_payload = _cart_payload(seeded, qty=1, expected_revision=None)
    cart_payload["tenders"] = [
        {"tender_type": "STORE_CREDIT", "amount": "50"},
        {"tender_type": "TAIWAN_PAY", "amount": "70"},
    ]
    created = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=cart_payload,
    )
    frozen = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/freeze-for-signature",
        headers=_auth(seeded.manager_token),
        json={"expected_revision": created.json()["revision"]},
    )
    frozen_body = frozen.json()
    task_id = frozen_body["signature_task_id"]
    await client.post(
        f"/api/v1/kiosk/tasks/{task_id}/ack",
        headers={"X-CSRF-Token": csrf},
    )
    signed = await client.post(
        f"/api/v1/kiosk/tasks/{task_id}/sign",
        headers={"X-CSRF-Token": csrf},
        json={
            "signature_image_base64": _signature_png(),
            "idempotency_key": "checkout-sign",
        },
    )
    assert signed.status_code == 200

    sale = await client.post(
        "/api/v1/sales",
        headers={
            **_auth(seeded.manager_token),
            "Idempotency-Key": "signed-checkout-13",
        },
        json={
            "lines": [
                {
                    "line_type": "CATALOG",
                    "catalog_product_id": seeded.product_id,
                    "qty": 1,
                }
            ],
            "buyer_contact_id": seeded.member_id,
            "tenders": [
                {"tender_type": "STORE_CREDIT", "amount": "50"},
                {"tender_type": "TAIWAN_PAY", "amount": "70"},
            ],
            "signature_task_id": task_id,
            "cart_session_id": frozen_body["cart"]["id"],
            "cart_revision": frozen_body["cart"]["revision"],
            "expected_einvoice_enabled": False,
        },
    )
    assert sale.status_code == 201, sale.text
    task = await db_session.get(SignatureTask, task_id)
    assert task is not None
    assert task.status.value == "CONSUMED"
    assert task.consumed_at is not None
    cart = await db_session.get(CartSession, frozen_body["cart"]["id"])
    assert cart is not None
    assert cart.status.value == "COMPLETED"
    assert cart.sale_id == sale.json()["id"]
    assert cart.snapshot["member"] is None

    success_screen = await client.get("/api/v1/kiosk/cart/current")
    assert success_screen.status_code == 200
    assert success_screen.json()["status"] == "COMPLETED"
    assert success_screen.json()["snapshot"]["member"] is None
    current_task = await client.get("/api/v1/kiosk/tasks/current")
    assert current_task.status_code == 200
    assert current_task.json() is None


class _UncertainLinePayTransport(LinePayTransport):
    def __init__(self) -> None:
        self.check_response: dict[str, object] = {
            "returnCode": "1150",
            "returnMessage": "Transaction record not found.",
        }
        self.check_calls = 0
        self.pay_calls = 0

    async def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: str | None,
    ) -> dict[str, object]:
        if url.endswith("/check"):
            self.check_calls += 1
            return self.check_response
        if url.endswith("/oneTimeKeys/pay"):
            self.pay_calls += 1
            raise LinePayTransportError("模擬 LINE Pay 回應逾時")
        raise AssertionError(f"未預期的 LINE Pay URL：{url}")


class _SuccessfulLinePayTransport(_UncertainLinePayTransport):
    async def send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: str | None,
    ) -> dict[str, object]:
        if url.endswith("/check"):
            self.check_calls += 1
            return self.check_response
        if url.endswith("/oneTimeKeys/pay"):
            self.pay_calls += 1
            return {
                "returnCode": "0000",
                "returnMessage": "Success.",
                "info": {"transactionId": 2026072400000000099},
            }
        raise AssertionError(f"未預期的 LINE Pay URL：{url}")


def _uncertain_linepay_client(transport: LinePayTransport) -> LinePayClient:
    return LinePayClient(
        channel_id="test-channel",
        channel_secret="test-secret",
        base_url="https://sandbox-api-pay.line.me",
        transport=transport,
        nonce_factory=lambda: "fixed-nonce",
    )


async def _prepare_signed_linepay_cart(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    *,
    suffix: str,
) -> tuple[Seeded, int, int, int, dict[str, object]]:
    seeded = await _seed(db_session, suffix)
    terminal_id, _, csrf = await _pair(client, seeded, suffix=suffix)
    manager = await db_session.scalar(select(User).where(User.username == f"cart-manager-{suffix}"))
    assert manager is not None
    await StoreSettingsService(db_session).update_settings(
        manager.store_id,
        actor_user_id=manager.id,
        patch=SettingsUpdateRequest(linepay_enabled=True),
    )
    await StoreCreditService(db_session).adjust(
        manager.store_id,
        seeded.member_id,
        amount=Decimal("100"),
        reason="PAYMENT_UNCERTAIN 測試入帳",
        created_by=manager.id,
        idempotency_key=f"uncertain-credit-{suffix}",
    )
    await db_session.commit()
    payload = _cart_payload(seeded, qty=1, expected_revision=None)
    payload["tenders"] = [
        {"tender_type": "STORE_CREDIT", "amount": "50"},
        {
            "tender_type": "LINE_PAY",
            "amount": "70",
            "line_pay_one_time_key": "OTK-uncertain",
        },
    ]
    created = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=payload,
    )
    assert created.status_code == 200, created.text
    frozen = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/freeze-for-signature",
        headers=_auth(seeded.manager_token),
        json={"expected_revision": created.json()["revision"]},
    )
    assert frozen.status_code == 200, frozen.text
    task_id = int(frozen.json()["signature_task_id"])
    assert (
        await client.post(
            f"/api/v1/kiosk/tasks/{task_id}/ack",
            headers={"X-CSRF-Token": csrf},
        )
    ).status_code == 200
    signed = await client.post(
        f"/api/v1/kiosk/tasks/{task_id}/sign",
        headers={"X-CSRF-Token": csrf},
        json={
            "signature_image_base64": _signature_png(),
            "idempotency_key": f"uncertain-sign-{suffix}",
        },
    )
    assert signed.status_code == 200, signed.text
    sale_payload: dict[str, object] = {
        "lines": [
            {
                "line_type": "CATALOG",
                "catalog_product_id": seeded.product_id,
                "qty": 1,
            }
        ],
        "buyer_contact_id": seeded.member_id,
        "tenders": payload["tenders"],
        "signature_task_id": task_id,
        "cart_session_id": frozen.json()["cart"]["id"],
        "cart_revision": frozen.json()["cart"]["revision"],
        "expected_einvoice_enabled": False,
    }
    return seeded, manager.store_id, terminal_id, task_id, sale_payload


async def test_post_charge_local_failure_enters_uncertain_instead_of_failing_signature(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded, store_id, _terminal_id, task_id, sale_payload = await _prepare_signed_linepay_cart(
        client,
        db_session,
        suffix="17",
    )
    manager = await db_session.scalar(select(User).where(User.username == "cart-manager-17"))
    assert manager is not None
    # 簽後餘額漂移會在 LINE Pay 成功後才由鎖定餘額檢查發現；此時不得把任務誤判 FAILED。
    await StoreCreditService(db_session).adjust(
        store_id,
        seeded.member_id,
        amount=Decimal("1"),
        reason="模擬簽後餘額漂移",
        created_by=manager.id,
        idempotency_key="post-charge-balance-drift",
    )
    await db_session.commit()
    transport = _SuccessfulLinePayTransport()
    linepay_client = _uncertain_linepay_client(transport)
    monkeypatch.setattr("app.modules.sales.router._linepay_client", lambda: linepay_client)

    response = await client.post(
        "/api/v1/sales",
        headers={
            **_auth(seeded.manager_token),
            "Idempotency-Key": "post-charge-local-failure",
        },
        json=sale_payload,
    )

    assert response.status_code == 409
    assert "PAYMENT_UNCERTAIN" in response.text
    task = await db_session.get(SignatureTask, task_id)
    cart = await db_session.get(CartSession, int(sale_payload["cart_session_id"]))
    assert task is not None and cart is not None
    await db_session.refresh(task)
    await db_session.refresh(cart)
    assert task.status.value == "SIGNED"
    assert task.expires_at is None
    assert cart.status.value == "PAYMENT_UNCERTAIN"
    assert transport.pay_calls == 1


async def test_linepay_transport_uncertainty_pauses_ttl_and_provider_success_recovers(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded, store_id, terminal_id, task_id, sale_payload = await _prepare_signed_linepay_cart(
        client,
        db_session,
        suffix="14",
    )
    transport = _UncertainLinePayTransport()
    linepay_client = _uncertain_linepay_client(transport)
    monkeypatch.setattr("app.modules.sales.router._linepay_client", lambda: linepay_client)
    monkeypatch.setattr(
        "app.modules.sales.linepay.linepay_client_from_config",
        lambda: linepay_client,
    )
    headers = {
        **_auth(seeded.manager_token),
        "Idempotency-Key": "uncertain-linepay-order",
    }
    uncertain = await client.post("/api/v1/sales", headers=headers, json=sale_payload)
    assert uncertain.status_code == 409
    assert "PAYMENT_UNCERTAIN" in uncertain.text
    cart_session_id = sale_payload["cart_session_id"]
    assert isinstance(cart_session_id, int)
    cart = await db_session.get(CartSession, cart_session_id)
    task = await db_session.get(SignatureTask, task_id)
    assert cart is not None and task is not None
    await db_session.refresh(cart)
    await db_session.refresh(task)
    assert cart.status.value == "PAYMENT_UNCERTAIN"
    assert cart.payment_order_id is not None
    assert cart.payment_checkout_payload is not None
    assert "line_pay_one_time_key" not in str(cart.payment_checkout_payload)
    assert task.status.value == "SIGNED"
    assert task.expires_at is None
    forbidden_void = await client.post(
        f"/api/v1/signing/tasks/{task_id}/cancel",
        headers=_auth(seeded.manager_token),
        json={
            "reason_code": "KIOSK_FAILURE_CASH_FALLBACK",
            "reason": "付款不明期間不得改現金",
        },
    )
    assert forbidden_void.status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(Sale)) == 0
    product = await db_session.get(CatalogProduct, seeded.product_id)
    assert product is not None and product.quantity_on_hand == 20
    assert await StoreCreditService(db_session).get_balance(store_id, seeded.member_id) == Decimal(
        "100"
    )
    assert (
        await SigningService(db_session).sweep_expired_tasks(
            now=datetime.now(UTC) + timedelta(days=30)
        )
        == 0
    )

    transport.check_response = {
        "returnCode": "0000",
        "returnMessage": "Success.",
        "info": {
            "transactionId": 2026072400000000001,
            "status": "COMPLETE",
            "payInfo": [{"method": "LINE_PAY", "amount": 70}],
        },
    }
    reconciled = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/reconcile-payment",
        headers=_auth(seeded.manager_token),
        json={"action": "QUERY_PROVIDER"},
    )
    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["outcome"] == "SUCCESS_CONFIRMED"
    assert reconciled.json()["cart"]["status"] == "COMPLETED"
    assert reconciled.json()["cart"]["sale_id"] is not None
    # 對帳確認成功即以後端保存且已剝除一次性碼的原請求補成立本機交易；不依賴原瀏覽器。
    await db_session.refresh(task)
    await db_session.refresh(cart)
    await db_session.refresh(product)
    assert task.status.value == "CONSUMED"
    assert cart.status.value == "COMPLETED"
    assert cart.payment_checkout_payload is None
    assert product.quantity_on_hand == 19
    assert await StoreCreditService(db_session).get_balance(store_id, seeded.member_id) == Decimal(
        "50"
    )
    assert transport.pay_calls == 1
    assert transport.check_calls == 2


async def test_payment_uncertain_manual_resolution_requires_manager_and_evidence(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded, store_id, terminal_id, task_id, sale_payload = await _prepare_signed_linepay_cart(
        client,
        db_session,
        suffix="15",
    )
    transport = _UncertainLinePayTransport()
    linepay_client = _uncertain_linepay_client(transport)
    monkeypatch.setattr("app.modules.sales.router._linepay_client", lambda: linepay_client)
    monkeypatch.setattr(
        "app.modules.sales.linepay.linepay_client_from_config",
        lambda: linepay_client,
    )
    headers = {
        **_auth(seeded.manager_token),
        "Idempotency-Key": "uncertain-manual-order",
    }
    assert (
        await client.post("/api/v1/sales", headers=headers, json=sale_payload)
    ).status_code == 409

    clerk = User(
        store_id=store_id,
        username="uncertain-clerk",
        password_hash="h",
        role=UserRole.CLERK,
    )
    db_session.add(clerk)
    await db_session.commit()
    clerk_token = encode_access_token(
        user_id=clerk.id,
        role=clerk.role.value,
        store_id=store_id,
    )
    forbidden = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/reconcile-payment",
        headers=_auth(clerk_token),
        json={
            "action": "MANUAL_FAILED",
            "reason": "後台確認未扣款",
            "evidence_type": "LINE_PAY_CONSOLE",
            "evidence_reference": "order-404",
        },
    )
    assert forbidden.status_code == 403
    missing_evidence = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/reconcile-payment",
        headers=_auth(seeded.manager_token),
        json={"action": "MANUAL_FAILED"},
    )
    assert missing_evidence.status_code == 422

    transport.check_response = {
        "returnCode": "0000",
        "returnMessage": "Success.",
        "info": {"status": "AUTH_READY"},
    }
    resolved = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/reconcile-payment",
        headers=_auth(seeded.manager_token),
        json={
            "action": "MANUAL_FAILED",
            "reason": "LINE Pay 後台與收單對帳均未見扣款",
            "evidence_type": "LINE_PAY_CONSOLE",
            "evidence_reference": "uncertain-manual-order",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["outcome"] == "FAILED_CONFIRMED"
    assert resolved.json()["cart"]["status"] == "DRAFT"
    task = await db_session.get(SignatureTask, task_id)
    assert task is not None
    await db_session.refresh(task)
    assert task.status.value == "FAILED"
    assert task.failure_reason is not None


async def test_payment_uncertain_manual_success_auto_creates_sale_without_recharging(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded, _store_id, terminal_id, task_id, sale_payload = await _prepare_signed_linepay_cart(
        client,
        db_session,
        suffix="16",
    )
    transport = _UncertainLinePayTransport()
    linepay_client = _uncertain_linepay_client(transport)
    monkeypatch.setattr("app.modules.sales.router._linepay_client", lambda: linepay_client)
    monkeypatch.setattr(
        "app.modules.sales.linepay.linepay_client_from_config",
        lambda: linepay_client,
    )
    assert (
        await client.post(
            "/api/v1/sales",
            headers={
                **_auth(seeded.manager_token),
                "Idempotency-Key": "uncertain-manual-success",
            },
            json=sale_payload,
        )
    ).status_code == 409

    transport.check_response = {
        "returnCode": "0000",
        "returnMessage": "Success.",
        "info": {"status": "AUTH_READY"},
    }
    resolved = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/reconcile-payment",
        headers=_auth(seeded.manager_token),
        json={
            "action": "MANUAL_SUCCESS",
            "reason": "LINE Pay 後台已確認入帳",
            "evidence_type": "LINE_PAY_CONSOLE",
            "evidence_reference": "2026072400000000999",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["outcome"] == "SUCCESS_CONFIRMED"
    assert resolved.json()["cart"]["status"] == "COMPLETED"
    sale_id = resolved.json()["cart"]["sale_id"]
    assert sale_id is not None
    transaction = await db_session.scalar(
        select(LinePayTransaction).where(LinePayTransaction.sale_id == sale_id)
    )
    assert transaction is not None
    assert transaction.amount == Decimal("70")
    assert transaction.raw_response["manual_reconciliation"] is True
    task = await db_session.get(SignatureTask, task_id)
    assert task is not None
    await db_session.refresh(task)
    assert task.status.value == "CONSUMED"
    assert transport.pay_calls == 1
    assert transport.check_calls == 2


async def test_revision_rejects_stale_overwrite_but_replays_same_lost_response(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, "2")
    terminal_id, _, _ = await _pair(client, seeded, suffix="2")
    created = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=_cart_payload(seeded, qty=1, expected_revision=None),
    )
    assert created.status_code == 200

    updated = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=_cart_payload(seeded, qty=3, expected_revision=1),
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["revision"] == 2
    assert body["snapshot"]["total"] == "360"
    assert body["changes"] == [
        {
            "type": "QUANTITY_CHANGED",
            "item_key": f"CATALOG:{seeded.product_id}",
            "name": "營釘補充包",
            "from_qty": 1,
            "to_qty": 3,
        }
    ]

    # 回應遺失後以同 expected_revision＋同內容重送：回放 revision 2，不再加版本。
    replay = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=_cart_payload(seeded, qty=3, expected_revision=1),
    )
    assert replay.status_code == 200
    assert replay.json()["revision"] == 2

    stale = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=_cart_payload(seeded, qty=2, expected_revision=1),
    )
    assert stale.status_code == 409
    assert "版本" in stale.json()["detail"]


async def test_cancel_draft_clears_kiosk_and_cannot_be_mutated_again(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, "3")
    terminal_id, _, _ = await _pair(client, seeded, suffix="3")
    created = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=_cart_payload(seeded, qty=1, expected_revision=None),
    )
    cart_id = created.json()["id"]

    cancelled = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/cancel",
        headers=_auth(seeded.manager_token),
        json={"expected_revision": 1, "reason": "店員清空購物車"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["id"] == cart_id
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["revision"] == 2

    kiosk_read = await client.get("/api/v1/kiosk/cart/current")
    assert kiosk_read.status_code == 200
    assert kiosk_read.json() is None

    after_cancel = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=_cart_payload(seeded, qty=2, expected_revision=1),
    )
    # CANCELLED 是終態；同一 terminal 的下一筆必須以 expected_revision=null 建新 session。
    assert after_cancel.status_code == 409


async def test_cart_events_are_database_enforced_append_only(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, "4")
    terminal_id, _, _ = await _pair(client, seeded, suffix="4")
    created = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=_cart_payload(seeded, qty=1, expected_revision=None),
    )
    assert created.status_code == 200, created.text
    event_id = await db_session.scalar(select(CartSessionEvent.id))
    assert event_id is not None

    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(CartSessionEvent)
                .where(CartSessionEvent.id == event_id)
                .values(event_type="TAMPERED")
            )
            await db_session.flush()

    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                delete(CartSessionEvent).where(CartSessionEvent.id == event_id)
            )
            await db_session.flush()


async def test_heartbeat_records_exact_displayed_revision_for_terminal_status(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, "5")
    terminal_id, device_id, csrf = await _pair(client, seeded, suffix="5")
    created = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=_cart_payload(seeded, qty=1, expected_revision=None),
    )
    cart = created.json()

    heartbeat = await client.post(
        "/api/v1/kiosk/heartbeat",
        headers={"X-CSRF-Token": csrf},
        json={"current_session_id": cart["id"], "displayed_revision": 1},
    )
    assert heartbeat.status_code == 200, heartbeat.text
    device = await db_session.get(KioskDevice, device_id)
    assert device is not None
    assert device.displayed_cart_session_id == cart["id"]
    assert device.displayed_revision == 1

    terminal = await client.get(
        f"/api/v1/customer-display/terminals/{terminal_id}",
        headers=_auth(seeded.manager_token),
    )
    assert terminal.status_code == 200
    kiosk = terminal.json()["paired_kiosk"]
    assert kiosk["online"] is True
    assert kiosk["current_session_id"] == cart["id"]
    assert kiosk["displayed_revision"] == 1

    impossible = await client.post(
        "/api/v1/kiosk/heartbeat",
        headers={"X-CSRF-Token": csrf},
        json={"current_session_id": cart["id"], "displayed_revision": 99},
    )
    assert impossible.status_code == 409


async def test_draft_cart_expiry_thaws_terminal_and_emits_clear_event(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, "6")
    terminal_id, _, _ = await _pair(client, seeded, suffix="6")
    created = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=_cart_payload(seeded, qty=1, expected_revision=None),
    )
    cart_id = created.json()["id"]
    stale_at = datetime.now(UTC) - timedelta(minutes=31)
    await db_session.execute(
        update(CartSession).where(CartSession.id == cart_id).values(last_activity_at=stale_at)
    )
    await db_session.flush()

    expired = await CustomerDisplayService(db_session).sweep_expired_carts(now=datetime.now(UTC))
    assert expired == 1
    row = await db_session.get(CartSession, cart_id)
    assert row is not None
    assert row.status.value == "EXPIRED"
    event = await db_session.scalar(
        select(CartSessionEvent)
        .where(CartSessionEvent.cart_session_id == cart_id)
        .order_by(CartSessionEvent.revision.desc())
    )
    assert event is not None
    assert event.event_type == "CART_EXPIRED"
    assert event.payload["reason"] == "DRAFT_IDLE_TTL"

    cleared = await client.get("/api/v1/kiosk/cart/current")
    assert cleared.status_code == 200
    assert cleared.json() is None


async def test_stale_processing_cart_recovers_to_draft(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, "32")
    terminal_id, _, _ = await _pair(client, seeded, suffix="32")
    created = await client.put(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart",
        headers=_auth(seeded.manager_token),
        json=_cart_payload(seeded, qty=1, expected_revision=None),
    )
    processing = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/begin-checkout",
        headers=_auth(seeded.manager_token),
        json={
            "expected_revision": created.json()["revision"],
            "signature_task_id": None,
        },
    )
    assert processing.status_code == 200
    cart_id = processing.json()["id"]
    stale_at = datetime.now(UTC) - timedelta(minutes=3)
    await db_session.execute(
        update(CartSession).where(CartSession.id == cart_id).values(last_activity_at=stale_at)
    )
    await db_session.flush()

    recovered = await CustomerDisplayService(db_session).sweep_expired_carts(now=datetime.now(UTC))

    assert recovered == 1
    row = await db_session.get(CartSession, cart_id)
    assert row is not None
    assert row.status.value == "DRAFT"
    event = await db_session.scalar(
        select(CartSessionEvent)
        .where(CartSessionEvent.cart_session_id == cart_id)
        .order_by(CartSessionEvent.revision.desc())
    )
    assert event is not None
    assert event.event_type == "PAYMENT_PROCESSING_RECOVERED"
