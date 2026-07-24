"""Sales integration tests for mandatory authoritative-cart store-credit signing."""

import itertools
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_pii_cipher, national_id_blind_index
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.customerdisplay.models import CartSession
from app.modules.customerdisplay.schemas import CartUpsertRequest
from app.modules.customerdisplay.service import CustomerDisplayService
from app.modules.inventory.models import CatalogProduct
from app.modules.sales.models import Sale
from app.modules.signing.models import SignatureTask, SignatureTaskEvent
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import (
    PayoutMethod,
    SignatureTaskKind,
    SignatureTaskStatus,
    UserRole,
)
from app.shared.exceptions import SignatureContentMismatch
from tests.integration.customer_display_helpers import (
    SignedCartContext,
    ensure_paired_customer_display,
    prepare_signed_store_credit_cart,
    signature_png_base64,
)

_idem = itertools.count()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def override_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as api_client:
        yield api_client
    app.dependency_overrides.clear()


def _auth(token: str, *, key: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": key or f"signed-sale-{next(_idem)}",
    }


async def _seed(session: AsyncSession) -> tuple[str, int, int, int, int]:
    store = Store(name="購物金簽署門市")
    session.add(store)
    await session.flush()
    clerk = User(
        store_id=store.id,
        username=f"signed-clerk-{store.id}",
        password_hash="h",
        role=UserRole.CLERK,
    )
    member = Contact(
        store_id=store.id,
        name="王小明",
        roles=["MEMBER", "SELLER"],
        national_id_enc=get_pii_cipher().encrypt("A123456789"),
        national_id_blind_index=national_id_blind_index("A123456789"),
    )
    product = CatalogProduct(
        store_id=store.id,
        sku=f"SIGNED-{store.id}",
        name="露營燈",
        unit_price=Decimal("300"),
        quantity_on_hand=20,
    )
    session.add_all([clerk, member, product])
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    await StoreCreditService(session).adjust(
        store.id,
        member.id,
        amount=Decimal("2000"),
        reason="簽署整合測試入帳",
        created_by=clerk.id,
        idempotency_key=f"signed-credit-{store.id}",
    )
    await session.commit()
    token = encode_access_token(
        user_id=clerk.id,
        role=clerk.role.value,
        store_id=store.id,
    )
    return token, store.id, clerk.id, member.id, product.id


def _base_payload(
    product_id: int,
    member_id: int,
    *,
    qty: int = 1,
    credit: str = "300",
    cash: str | None = None,
) -> dict[str, object]:
    tenders: list[dict[str, str]] = [{"tender_type": "STORE_CREDIT", "amount": credit}]
    if cash is not None:
        tenders.append({"tender_type": "CASH", "amount": cash})
    return {
        "lines": [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": qty}],
        "buyer_contact_id": member_id,
        "tenders": tenders,
    }


def _with_context(
    payload: dict[str, object],
    context: SignedCartContext,
) -> dict[str, object]:
    return {
        **payload,
        "signature_task_id": context.signature_task_id,
        "cart_session_id": context.cart_session_id,
        "cart_revision": context.cart_revision,
    }


async def _signed(
    session: AsyncSession,
    *,
    store_id: int,
    clerk_id: int,
    payload: dict[str, object],
) -> SignedCartContext:
    return await prepare_signed_store_credit_cart(
        session,
        store_id=store_id,
        actor_user_id=clerk_id,
        payload=payload,
    )


async def test_store_credit_sale_consumes_one_signed_snapshot(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, clerk_id, member_id, product_id = await _seed(db_session)
    payload = _base_payload(product_id, member_id)
    context = await _signed(
        db_session,
        store_id=store_id,
        clerk_id=clerk_id,
        payload=payload,
    )
    response = await client.post(
        "/api/v1/sales",
        json=_with_context(payload, context),
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    sale = await db_session.get(Sale, response.json()["id"])
    assert sale is not None
    assert sale.signature_task_id == context.signature_task_id
    task = await db_session.get(SignatureTask, context.signature_task_id)
    assert task is not None
    assert task.status is SignatureTaskStatus.CONSUMED
    assert task.consumed_at is not None
    assert await StoreCreditService(db_session).get_balance(store_id, member_id) == Decimal("1700")


async def test_unsigned_store_credit_is_rejected_even_when_legacy_setting_is_false(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, _store_id, _clerk_id, member_id, product_id = await _seed(db_session)
    unsigned = await client.post(
        "/api/v1/sales",
        json=_base_payload(product_id, member_id),
        headers=_auth(token),
    )
    assert unsigned.status_code == 422
    assert "已凍結" in unsigned.text

    cash = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 1}],
            "buyer_contact_id": member_id,
            "tenders": [{"tender_type": "CASH", "amount": "300"}],
        },
        headers=_auth(token),
    )
    assert cash.status_code == 201, cash.text


@pytest.mark.parametrize(
    "mutation",
    [
        "credit_amount",
        "quantity",
        "member",
        "payment_split",
    ],
)
async def test_checkout_rejects_any_change_after_signing(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    mutation: str,
) -> None:
    token, store_id, clerk_id, member_id, product_id = await _seed(db_session)
    payload = _base_payload(product_id, member_id)
    context = await _signed(
        db_session,
        store_id=store_id,
        clerk_id=clerk_id,
        payload=payload,
    )
    changed = _with_context(payload, context)
    if mutation == "credit_amount":
        changed["tenders"] = [
            {"tender_type": "STORE_CREDIT", "amount": "200"},
            {"tender_type": "CASH", "amount": "100"},
        ]
    elif mutation == "quantity":
        changed["lines"] = [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 2}]
        changed["tenders"] = [{"tender_type": "STORE_CREDIT", "amount": "600"}]
    elif mutation == "member":
        other = Contact(store_id=store_id, name="其他會員", roles=["MEMBER"])
        db_session.add(other)
        await db_session.commit()
        changed["buyer_contact_id"] = other.id
    else:
        changed["tenders"] = [
            {"tender_type": "CASH", "amount": "300"},
        ]
    response = await client.post(
        "/api/v1/sales",
        json=changed,
        headers=_auth(token),
    )
    assert response.status_code == 422, response.text
    assert await StoreCreditService(db_session).get_balance(store_id, member_id) == Decimal("2000")


async def test_same_signed_snapshot_replays_only_the_original_sale(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, clerk_id, member_id, product_id = await _seed(db_session)
    payload = _base_payload(product_id, member_id)
    context = await _signed(
        db_session,
        store_id=store_id,
        clerk_id=clerk_id,
        payload=payload,
    )
    body = _with_context(payload, context)
    first = await client.post("/api/v1/sales", json=body, headers=_auth(token))
    replay = await client.post("/api/v1/sales", json=body, headers=_auth(token))
    assert first.status_code == replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    assert await StoreCreditService(db_session).get_balance(store_id, member_id) == Decimal("1700")

    changed = {
        **body,
        "lines": [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 2}],
        "tenders": [
            {"tender_type": "STORE_CREDIT", "amount": "300"},
            {"tender_type": "CASH", "amount": "300"},
        ],
    }
    conflict = await client.post("/api/v1/sales", json=changed, headers=_auth(token))
    assert conflict.status_code == 409


async def test_balance_change_after_signing_invalidates_checkout(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, clerk_id, member_id, product_id = await _seed(db_session)
    payload = _base_payload(product_id, member_id)
    context = await _signed(
        db_session,
        store_id=store_id,
        clerk_id=clerk_id,
        payload=payload,
    )
    await StoreCreditService(db_session).adjust(
        store_id,
        member_id,
        amount=Decimal("500"),
        reason="簽署後餘額變動",
        created_by=clerk_id,
        idempotency_key=f"balance-drift-{store_id}",
    )
    await db_session.commit()
    response = await client.post(
        "/api/v1/sales",
        json=_with_context(payload, context),
        headers=_auth(token),
    )
    assert response.status_code == 422
    assert "餘額已變動" in response.text
    assert await StoreCreditService(db_session).get_balance(store_id, member_id) == Decimal("2500")


async def test_void_signed_task_thaws_cart_in_same_transaction(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, clerk_id, member_id, product_id = await _seed(db_session)
    payload = _base_payload(product_id, member_id)
    context = await _signed(
        db_session,
        store_id=store_id,
        clerk_id=clerk_id,
        payload=payload,
    )
    response = await client.post(
        f"/api/v1/signing/tasks/{context.signature_task_id}/cancel",
        headers=_auth(token),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "VOIDED"
    cart = await db_session.get(CartSession, context.cart_session_id)
    assert cart is not None
    await db_session.refresh(cart)
    assert cart.status.value == "DRAFT"
    assert cart.active_signature_task_id is None

    checkout = await client.post(
        "/api/v1/sales",
        json=_with_context(payload, context),
        headers=_auth(token),
    )
    assert checkout.status_code == 422


async def test_signed_task_ttl_expires_and_thaws_cart(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, clerk_id, member_id, product_id = await _seed(db_session)
    payload = _base_payload(product_id, member_id)
    context = await _signed(
        db_session,
        store_id=store_id,
        clerk_id=clerk_id,
        payload=payload,
    )
    await db_session.execute(
        text("UPDATE signature_tasks SET expires_at=now() - interval '1 second' WHERE id=:task_id"),
        {"task_id": context.signature_task_id},
    )
    await db_session.commit()
    response = await client.post(
        "/api/v1/sales",
        json=_with_context(payload, context),
        headers=_auth(token),
    )
    assert response.status_code == 422
    task = await db_session.get(SignatureTask, context.signature_task_id)
    assert task is not None and task.status is SignatureTaskStatus.EXPIRED
    cart = await db_session.get(CartSession, context.cart_session_id)
    assert cart is not None
    await db_session.refresh(cart)
    assert cart.status.value == "DRAFT"


async def test_signed_content_is_customer_visible_canonical_snapshot(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _token, store_id, clerk_id, member_id, product_id = await _seed(db_session)
    payload = _base_payload(product_id, member_id, credit="200", cash="100")
    context = await _signed(
        db_session,
        store_id=store_id,
        clerk_id=clerk_id,
        payload=payload,
    )
    task = await db_session.get(SignatureTask, context.signature_task_id)
    assert task is not None
    assert task.content["member"] == {"display_name": "王○明"}
    assert task.content["total"] == "300"
    assert task.content["store_credit_amount"] == "200"
    assert task.content["store_credit_balance_before"] == "2000"
    assert task.content["store_credit_balance_after"] == "1800"
    assert task.content["remaining_tenders"] == [{"tender_type": "CASH", "amount": "100"}]
    assert task.content["items"] == [
        {
            "name": "露營燈",
            "unit_price": "300",
            "qty": 1,
            "original_unit_price": None,
            "discount_amount": "0",
            "line_total": "300",
        }
    ]
    assert "phone" not in task.content
    assert "item_key" not in task.content["items"][0]


async def test_affidavit_signature_cannot_substitute_for_store_credit_signature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, clerk_id, member_id, product_id = await _seed(db_session)
    terminal, device = await ensure_paired_customer_display(
        db_session,
        store_id=store_id,
        actor_user_id=clerk_id,
    )
    signing = SigningService(db_session)
    affidavit = await signing.create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
            contact_id=member_id,
            terminal_id=terminal.id,
            content={"items": [{"name": "舊帳篷", "amount": "300"}], "total": "300"},
        ),
        created_by=clerk_id,
    )
    await signing.acknowledge_task(store_id, device.id, affidavit.id)
    await signing.sign_task(
        store_id,
        affidavit.id,
        device_id=device.id,
        signature_image_base64=signature_png_base64(),
        chosen_payout=PayoutMethod.STORE_CREDIT,
    )
    await signing.cancel_task(
        store_id,
        affidavit.id,
        actor_user_id=clerk_id,
        reason="建立下一個隔離測試任務",
    )
    await db_session.commit()

    payload = _base_payload(product_id, member_id)
    context = await _signed(
        db_session,
        store_id=store_id,
        clerk_id=clerk_id,
        payload=payload,
    )
    wrong = _with_context(payload, context)
    wrong["signature_task_id"] = affidavit.id
    response = await client.post("/api/v1/sales", json=wrong, headers=_auth(token))
    assert response.status_code == 422


async def test_transaction_ack_is_canonical_and_consumed_in_same_post(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, clerk_id, member_id, product_id = await _seed(db_session)
    terminal, device = await ensure_paired_customer_display(
        db_session,
        store_id=store_id,
        actor_user_id=clerk_id,
    )
    display = CustomerDisplayService(db_session)
    cart = await display.upsert_cart(
        store_id,
        terminal.id,
        CartUpsertRequest.model_validate(
            {
                "expected_revision": None,
                "lines": [
                    {
                        "line_type": "CATALOG",
                        "catalog_product_id": product_id,
                        "qty": 1,
                    }
                ],
                "buyer_contact_id": member_id,
                "tenders": [{"tender_type": "CASH", "amount": "300"}],
            }
        ),
        actor_user_id=clerk_id,
    )
    await db_session.commit()
    sale_response = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 1}],
            "buyer_contact_id": member_id,
            "tenders": [{"tender_type": "CASH", "amount": "300"}],
            "cart_session_id": cart.id,
            "cart_revision": cart.revision,
        },
        headers=_auth(token),
    )
    assert sale_response.status_code == 201, sale_response.text
    sale_id = int(sale_response.json()["id"])

    signing = SigningService(db_session)
    task = await signing.create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.TRANSACTION_ACK,
            contact_id=member_id,
            terminal_id=terminal.id,
            content={"sale_ref": "#999", "total": "1", "note": "tampered"},
            ref_type="sale",
            ref_id=sale_id,
        ),
        created_by=clerk_id,
    )
    assert task.content["sale_ref"] == f"#{sale_id}"
    assert task.content["total"] == "300"
    assert "note" not in task.content
    await signing.acknowledge_task(store_id, device.id, task.id)
    task = await signing.sign_task(
        store_id,
        task.id,
        device_id=device.id,
        signature_image_base64=signature_png_base64(),
        chosen_payout=None,
    )
    await db_session.commit()
    assert task.status is SignatureTaskStatus.CONSUMED
    events = list(
        (
            await db_session.scalars(
                select(SignatureTaskEvent.to_status)
                .where(SignatureTaskEvent.signature_task_id == task.id)
                .order_by(SignatureTaskEvent.id)
            )
        ).all()
    )
    assert events[-2:] == [SignatureTaskStatus.SIGNED, SignatureTaskStatus.CONSUMED]


async def test_transaction_ack_revalidates_sale_and_device_ownership(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token, store_id, clerk_id, member_id, product_id = await _seed(db_session)
    cash_sale = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 1}],
            "buyer_contact_id": member_id,
            "tenders": [{"tender_type": "CASH", "amount": "300"}],
        },
        headers=_auth(token),
    )
    assert cash_sale.status_code == 201
    terminal, _device = await ensure_paired_customer_display(
        db_session,
        store_id=store_id,
        actor_user_id=clerk_id,
    )
    with pytest.raises(SignatureContentMismatch, match=r"客顯|櫃檯"):
        await SigningService(db_session).create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.TRANSACTION_ACK,
                contact_id=member_id,
                terminal_id=terminal.id,
                content={},
                ref_type="sale",
                ref_id=int(cash_sale.json()["id"]),
            ),
            created_by=clerk_id,
        )
