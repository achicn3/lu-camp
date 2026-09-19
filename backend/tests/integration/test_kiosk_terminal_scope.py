"""客顯換配對後，不得再看到或簽掉**原本那台櫃檯**的東西（2026-09-19 審查 M1）。

既有的檢查只問「這台平板還在配對中嗎」，沒問「配對到哪一台櫃檯」。店裡只要有第二台
櫃檯（例如收購平板自己開一個店務分頁），就會出現：

    平板 A 在櫃檯①有一台進行中的車 → 店員解除配對 → A 改配到櫃檯②
    → A 仍讀得到櫃檯①的購物車（含會員姓名與金額），甚至簽得下櫃檯①推來的任務。

這違反 CLAUDE.md §4（不得寫死只有一台收銀台）與 docs/23 的最小權限。
"""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_pii_cipher, national_id_blind_index
from app.core.db import get_session
from app.core.security import encode_access_token, hash_password
from app.main import create_app
from app.modules.contacts.models import Contact
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


@dataclass
class Seeded:
    store_id: int
    manager_token: str
    kiosk_username: str
    contact_id: int
    product_id: int
    device_id: int = 0
    csrf_token: str = ""


async def _seed(session: AsyncSession) -> Seeded:
    store = Store(name="配對範圍測試店")
    session.add(store)
    await session.flush()
    manager = User(
        store_id=store.id,
        username=f"scope-manager-{store.id}",
        password_hash=hash_password("manager-secret"),
        role=UserRole.MANAGER,
    )
    kiosk = User(
        store_id=store.id,
        username=f"scope-kiosk-{store.id}",
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
    product = CatalogProduct(
        store_id=store.id,
        sku=f"SCOPE-{store.id}",
        name="營釘補充包",
        unit_price=Decimal(120),
        quantity_on_hand=20,
    )
    session.add_all([manager, kiosk, contact, product])
    await session.flush()
    await session.commit()
    return Seeded(
        store_id=store.id,
        manager_token=encode_access_token(
            user_id=manager.id, role=manager.role.value, store_id=store.id
        ),
        kiosk_username=kiosk.username,
        contact_id=contact.id,
        product_id=product.id,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _csrf(seeded: Seeded) -> dict[str, str]:
    return {"X-CSRF-Token": seeded.csrf_token}


async def _kiosk_login(client: httpx.AsyncClient, seeded: Seeded) -> Seeded:
    """平板登入一次；解除配對不會讓這個 session 失效（刻意設計）。"""
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
    body = login.json()
    seeded.device_id = int(body["device_id"])
    seeded.csrf_token = str(body["csrf_token"])
    return seeded


async def _register_terminal(client: httpx.AsyncClient, seeded: Seeded, *, n: int) -> int:
    response = await client.post(
        "/api/v1/customer-display/terminals",
        headers=_auth(seeded.manager_token),
        json={
            "installation_id": f"1000000{n}-0000-4000-8000-{seeded.store_id:012d}",
            "name": f"櫃檯 {n}",
        },
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


async def _pair(client: httpx.AsyncClient, seeded: Seeded, terminal_id: int) -> None:
    """平板自己要一組配對碼，店員在該櫃檯輸入——與實機流程相同。"""
    code_response = await client.post("/api/v1/kiosk/pairing-codes", headers=_csrf(seeded))
    assert code_response.status_code == 201, code_response.text
    code = code_response.json()["pairing_code"]
    assert isinstance(code, str)
    paired = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/pair",
        headers=_auth(seeded.manager_token),
        json={"pairing_code": code},
    )
    assert paired.status_code == 200, paired.text


async def _unpair(client: httpx.AsyncClient, seeded: Seeded, terminal_id: int) -> None:
    response = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_id}/unpair",
        headers=_auth(seeded.manager_token),
        json={"reason": "平板改配到另一台櫃檯"},
    )
    assert response.status_code == 200, response.text


async def _kiosk_json(client: httpx.AsyncClient, path: str) -> Any:
    response = await client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


async def test_rebinding_to_another_terminal_hides_the_first_terminal_cart(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """換配對到第二台櫃檯後，讀不到第一台櫃檯的購物車。"""
    seeded = await _kiosk_login(client, await _seed(db_session))
    first = await _register_terminal(client, seeded, n=1)
    second = await _register_terminal(client, seeded, n=2)
    await _pair(client, seeded, first)

    put = await client.put(
        f"/api/v1/customer-display/terminals/{first}/cart",
        headers=_auth(seeded.manager_token),
        json={
            "expected_revision": None,
            "lines": [{"line_type": "CATALOG", "catalog_product_id": seeded.product_id, "qty": 1}],
            "buyer_contact_id": seeded.contact_id,
        },
    )
    assert put.status_code == 200, put.text
    assert await _kiosk_json(client, "/api/v1/kiosk/cart/current") is not None

    await _unpair(client, seeded, first)
    await _pair(client, seeded, second)

    after = await _kiosk_json(client, "/api/v1/kiosk/cart/current")
    assert after is None, "換配對後仍讀得到前一台櫃檯的購物車（含會員資料與金額）"


async def test_rebinding_hides_pending_task_from_the_first_terminal(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """換配對後，前一台櫃檯推來的簽署任務不得再出現、也不得再被簽掉。"""
    seeded = await _kiosk_login(client, await _seed(db_session))
    first = await _register_terminal(client, seeded, n=1)
    second = await _register_terminal(client, seeded, n=2)
    await _pair(client, seeded, first)

    created = await client.post(
        "/api/v1/signing/tasks",
        headers=_auth(seeded.manager_token),
        json={
            "kind": "ACQUISITION_AFFIDAVIT",
            "contact_id": seeded.contact_id,
            "terminal_id": first,
            "content": {
                "items": [{"name": "登山背包", "amount": "1200"}],
                "total": "1200",
            },
        },
    )
    assert created.status_code == 201, created.text
    task_id = int(created.json()["id"])
    assert await _kiosk_json(client, "/api/v1/kiosk/tasks/current") is not None

    await _unpair(client, seeded, first)
    await _pair(client, seeded, second)

    after = await _kiosk_json(client, "/api/v1/kiosk/tasks/current")
    assert after is None, "換配對後仍看得到前一台櫃檯的簽署任務"

    # 單看清單消失不夠：直接指名任務編號也必須拿不到、按不下去。
    detail = await client.get(f"/api/v1/kiosk/tasks/{task_id}")
    assert detail.status_code == 404, detail.text
    acked = await client.post(f"/api/v1/kiosk/tasks/{task_id}/ack", headers=_csrf(seeded))
    assert acked.status_code == 404, acked.text


async def test_the_new_terminal_can_still_push_its_own_task(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """守住修正的另一半：換配對後，新櫃檯自己的任務照樣看得到（別修成全部看不到）。"""
    seeded = await _kiosk_login(client, await _seed(db_session))
    first = await _register_terminal(client, seeded, n=1)
    second = await _register_terminal(client, seeded, n=2)
    await _pair(client, seeded, first)
    await _unpair(client, seeded, first)
    await _pair(client, seeded, second)

    created = await client.post(
        "/api/v1/signing/tasks",
        headers=_auth(seeded.manager_token),
        json={
            "kind": "ACQUISITION_AFFIDAVIT",
            "contact_id": seeded.contact_id,
            "terminal_id": second,
            "content": {
                "items": [{"name": "登山背包", "amount": "1200"}],
                "total": "1200",
            },
        },
    )
    assert created.status_code == 201, created.text
    current = await _kiosk_json(client, "/api/v1/kiosk/tasks/current")
    assert current is not None
    assert current["id"] == created.json()["id"]


async def test_heartbeat_rejects_a_cart_from_the_previous_terminal(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """換配對後，平板不得再把前一台櫃檯的車回報成「我正在顯示這張」。

    heartbeat 不回內容，所以這不是外洩；但店務端會照著它顯示「這台客顯正在顯示哪張車」，
    收不收斂要與其他裝置端入口一致。
    """
    seeded = await _kiosk_login(client, await _seed(db_session))
    first = await _register_terminal(client, seeded, n=1)
    second = await _register_terminal(client, seeded, n=2)
    await _pair(client, seeded, first)

    put = await client.put(
        f"/api/v1/customer-display/terminals/{first}/cart",
        headers=_auth(seeded.manager_token),
        json={
            "expected_revision": None,
            "lines": [{"line_type": "CATALOG", "catalog_product_id": seeded.product_id, "qty": 1}],
        },
    )
    assert put.status_code == 200, put.text
    cart = put.json()

    beat = await client.post(
        "/api/v1/kiosk/heartbeat",
        headers=_csrf(seeded),
        json={"current_session_id": cart["id"], "displayed_revision": cart["revision"]},
    )
    assert beat.status_code == 200, beat.text

    await _unpair(client, seeded, first)
    await _pair(client, seeded, second)

    stale = await client.post(
        "/api/v1/kiosk/heartbeat",
        headers=_csrf(seeded),
        json={"current_session_id": cart["id"], "displayed_revision": cart["revision"]},
    )
    assert stale.status_code == 409, stale.text
