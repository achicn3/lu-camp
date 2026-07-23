"""客顯即時購物車 session API。

公開行為守護：
- 第一件商品建立 DRAFT；價格、折扣與總額一律由 sales quote 在後端計算。
- 會員 payload 只有遮罩姓名，不含電話、證號或可逆 PII。
- revision 採 optimistic concurrency；舊 revision 不得覆蓋新資料，回應遺失的同內容重送可回放。
- POS 與配對客顯可各自恢復同一未完成 session；清空只從 DRAFT 轉 CANCELLED。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token, hash_password
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.customerdisplay.models import CartSession, CartSessionEvent, KioskDevice
from app.modules.customerdisplay.service import CustomerDisplayService
from app.modules.inventory.models import CatalogProduct
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole

ORIGIN = "http://localhost:3000"


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
    assert kiosk_read.json() == cart

    staff_restore = await client.get(
        f"/api/v1/customer-display/terminals/{terminal_id}/cart/current",
        headers=_auth(seeded.manager_token),
    )
    assert staff_restore.status_code == 200
    assert staff_restore.json() == cart


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
