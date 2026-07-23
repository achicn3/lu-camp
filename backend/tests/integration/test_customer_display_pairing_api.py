"""客顯裝置身分與櫃檯配對 API（客顯 v2 第一個垂直切片）。

從公開 HTTP 邊界驗證：
- KIOSK 帳密只換取 Path-scoped HttpOnly device-session cookie，不回 bearer token。
- KIOSK 變更型請求同時要求可信 Origin 與 session-bound CSRF token。
- POS 櫃檯與客顯以短效一次性代碼配對，代碼不可重放且不可跨店使用。
- 配對後由伺服器回傳唯一關聯；localStorage 不參與兩台瀏覽器間通訊。
"""

from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token, hash_password
from app.main import create_app
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
        store_id: int,
        manager_token: str,
        kiosk_username: str,
        kiosk_password: str,
    ) -> None:
        self.store_id = store_id
        self.manager_token = manager_token
        self.kiosk_username = kiosk_username
        self.kiosk_password = kiosk_password


async def _seed(
    session: AsyncSession,
    *,
    suffix: str,
    kiosk_password: str = "kiosk-secret",
) -> Seeded:
    store = Store(name=f"客顯測試店-{suffix}")
    session.add(store)
    await session.flush()
    manager = User(
        store_id=store.id,
        username=f"manager-{suffix}",
        password_hash=hash_password("manager-secret"),
        role=UserRole.MANAGER,
    )
    kiosk = User(
        store_id=store.id,
        username=f"kiosk-{suffix}",
        password_hash=hash_password(kiosk_password),
        role=UserRole.KIOSK,
    )
    session.add_all([manager, kiosk])
    await session.flush()
    await session.commit()
    return Seeded(
        store_id=store.id,
        manager_token=encode_access_token(
            user_id=manager.id,
            role=manager.role.value,
            store_id=store.id,
        ),
        kiosk_username=kiosk.username,
        kiosk_password=kiosk_password,
    )


def _staff_auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login_kiosk(
    client: httpx.AsyncClient,
    seeded: Seeded,
    *,
    installation_id: str,
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/kiosk/device-sessions",
        json={
            "username": seeded.kiosk_username,
            "password": seeded.kiosk_password,
            "installation_id": installation_id,
            "label": "顧客平板",
        },
    )
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


async def test_kiosk_login_sets_scoped_http_only_cookie_and_returns_pairing_code(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, suffix="cookie")

    body = await _login_kiosk(
        client,
        seeded,
        installation_id="70a07319-c065-4c14-8dc8-a60103c4a41b",
    )

    assert set(body) == {
        "device_id",
        "label",
        "csrf_token",
        "pairing_code",
        "pairing_code_expires_at",
        "paired_terminal",
    }
    assert isinstance(body["device_id"], int)
    assert body["label"] == "顧客平板"
    assert isinstance(body["csrf_token"], str) and len(body["csrf_token"]) >= 32
    assert isinstance(body["pairing_code"], str)
    assert len(body["pairing_code"]) == 6
    assert str(body["pairing_code"]).isdigit()
    assert body["paired_terminal"] is None
    set_cookie = response_cookie = client.cookies.get("lu_camp_kiosk_session")
    assert response_cookie is not None
    assert set_cookie is not None

    raw_cookie = client.cookies.jar
    cookie = next(c for c in raw_cookie if c.name == "lu_camp_kiosk_session")
    assert cookie.path == "/api/v1/kiosk"
    assert cookie.has_nonstandard_attr("HttpOnly")
    assert cookie.get_nonstandard_attr("SameSite").lower() == "strict"

    # Cookie Path 不涵蓋一般店務 API；沒有 bearer token 時仍須 401。
    general = await client.get("/api/v1/cash-sessions/current")
    assert general.status_code == 401


async def test_kiosk_mutation_requires_trusted_origin_and_session_csrf(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, suffix="csrf")
    login = await _login_kiosk(
        client,
        seeded,
        installation_id="30581ff0-3768-4292-84e7-351ab24b911f",
    )
    csrf = str(login["csrf_token"])

    missing_origin = await client.post(
        "/api/v1/kiosk/heartbeat",
        headers={"Origin": ""},
        json={"current_session_id": None, "displayed_revision": 0},
    )
    assert missing_origin.status_code == 403

    missing_csrf = await client.post(
        "/api/v1/kiosk/heartbeat",
        json={"current_session_id": None, "displayed_revision": 0},
    )
    assert missing_csrf.status_code == 403

    ok = await client.post(
        "/api/v1/kiosk/heartbeat",
        headers={"X-CSRF-Token": csrf},
        json={"current_session_id": None, "displayed_revision": 0},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["online"] is True
    assert ok.json()["last_seen_at"].endswith("Z")


async def test_staff_pairs_terminal_to_kiosk_with_one_time_code(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, suffix="pair")
    kiosk = await _login_kiosk(
        client,
        seeded,
        installation_id="a6e69c86-e9e6-4b1a-a7be-983107c32d34",
    )

    terminal_response = await client.post(
        "/api/v1/customer-display/terminals",
        headers=_staff_auth(seeded.manager_token),
        json={
            "installation_id": "6e38ac62-29e2-474e-be00-e5c88fe4ab52",
            "name": "主櫃檯",
        },
    )
    assert terminal_response.status_code == 201, terminal_response.text
    terminal = terminal_response.json()

    paired_response = await client.post(
        f"/api/v1/customer-display/terminals/{terminal['id']}/pair",
        headers=_staff_auth(seeded.manager_token),
        json={"pairing_code": kiosk["pairing_code"]},
    )
    assert paired_response.status_code == 200, paired_response.text
    paired = paired_response.json()
    assert paired["id"] == terminal["id"]
    assert paired["paired_kiosk"]["id"] == kiosk["device_id"]
    assert paired["paired_kiosk"]["label"] == "顧客平板"

    kiosk_status = await client.get("/api/v1/kiosk/device")
    assert kiosk_status.status_code == 200, kiosk_status.text
    assert kiosk_status.json()["paired_terminal"]["id"] == terminal["id"]
    assert kiosk_status.json()["pairing_code"] is None

    replay = await client.post(
        f"/api/v1/customer-display/terminals/{terminal['id']}/pair",
        headers=_staff_auth(seeded.manager_token),
        json={"pairing_code": kiosk["pairing_code"]},
    )
    assert replay.status_code == 409


async def test_pairing_code_cannot_cross_store(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    store_a = await _seed(db_session, suffix="store-a")
    store_b = await _seed(db_session, suffix="store-b")
    kiosk_a = await _login_kiosk(
        client,
        store_a,
        installation_id="ba320824-8fe4-48bf-aab7-d16476833033",
    )
    terminal_b_response = await client.post(
        "/api/v1/customer-display/terminals",
        headers=_staff_auth(store_b.manager_token),
        json={
            "installation_id": "a0a9ac66-82e9-4adf-b201-4c6914ae2952",
            "name": "他店櫃檯",
        },
    )
    assert terminal_b_response.status_code == 201
    terminal_b = terminal_b_response.json()

    cross_store = await client.post(
        f"/api/v1/customer-display/terminals/{terminal_b['id']}/pair",
        headers=_staff_auth(store_b.manager_token),
        json={"pairing_code": kiosk_a["pairing_code"]},
    )
    assert cross_store.status_code == 409
    assert "配對碼" in cross_store.json()["detail"]


async def test_relogin_same_installation_reuses_device_and_rotates_cookie(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    seeded = await _seed(db_session, suffix="relogin")
    installation_id = "22f71ad9-63dc-4571-ab55-9a4dcc5fab4e"

    first = await _login_kiosk(client, seeded, installation_id=installation_id)
    first_cookie = client.cookies.get("lu_camp_kiosk_session")
    second = await _login_kiosk(client, seeded, installation_id=installation_id)
    second_cookie = client.cookies.get("lu_camp_kiosk_session")

    assert second["device_id"] == first["device_id"]
    assert second_cookie is not None and second_cookie != first_cookie
