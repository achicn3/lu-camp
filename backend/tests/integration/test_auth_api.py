"""auth API 整合測試：POST /auth/login（帳密 → JWT access token）。

防使用者列舉：帳號不存在 / 密碼錯誤 / 帳號停用 一律回相同的 401 訊息。
"""

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import decode_access_token, hash_password
from app.main import create_app
from app.modules.store.models import Store
from app.modules.user.models import User
from app.modules.user.router import get_login_throttle
from app.modules.user.service import UserService
from app.modules.user.throttle import LoginThrottle
from app.shared.enums import UserRole


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    # 每個測試 app 用「同一個」獨立節流器實例：跨請求累積狀態，但不污染其他測試
    throttle = LoginThrottle()
    app.dependency_overrides[get_login_throttle] = lambda: throttle
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _seed_user(
    session: AsyncSession,
    *,
    username: str = "manager1",
    password: str = "pw-123456",
    role: UserRole = UserRole.MANAGER,
    is_active: bool = True,
) -> User:
    store = Store(name="測試門市")
    session.add(store)
    await session.flush()
    user = User(
        store_id=store.id,
        username=username,
        password_hash=hash_password(password),
        role=role,
        is_active=is_active,
    )
    session.add(user)
    await session.flush()
    return user


async def test_login_success_returns_decodable_token(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await _seed_user(db_session)
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "manager1", "password": "pw-123456"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    payload = decode_access_token(body["access_token"])
    assert payload["sub"] == str(user.id)
    assert payload["role"] == "MANAGER"
    assert payload["store_id"] == user.store_id


async def test_login_token_works_on_protected_endpoint(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """登入取得的 token 可通過既有受保護端點的認證（端到端打通）。"""
    await _seed_user(db_session, role=UserRole.CLERK, username="clerk1")
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "clerk1", "password": "pw-123456"}
    )
    token = resp.json()["access_token"]
    protected = await client.get(
        "/api/v1/cash-sessions/current", headers={"Authorization": f"Bearer {token}"}
    )
    assert protected.status_code != 401  # 認證通過（業務狀態碼不在此測）


async def test_login_wrong_password_401(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_user(db_session)
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "manager1", "password": "wrong"}
    )
    assert resp.status_code == 401


async def test_login_unknown_username_401_same_detail_as_wrong_password(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """防列舉：帳號不存在與密碼錯誤回相同訊息。"""
    await _seed_user(db_session)
    wrong_pw = await client.post(
        "/api/v1/auth/login", json={"username": "manager1", "password": "wrong"}
    )
    unknown = await client.post(
        "/api/v1/auth/login", json={"username": "no-such-user", "password": "wrong"}
    )
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == wrong_pw.json()["detail"]


async def test_login_inactive_user_401_same_detail(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_user(db_session, username="quit-user", is_active=False)
    wrong_pw_baseline = await client.post(
        "/api/v1/auth/login", json={"username": "no-such-user", "password": "x"}
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"username": "quit-user", "password": "pw-123456"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == wrong_pw_baseline.json()["detail"]


async def test_login_missing_fields_422(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/login", json={"username": "only-name"})
    assert resp.status_code == 422


async def test_login_throttled_after_repeated_failures(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """連續失敗達門檻 → 429 + Retry-After，**即使密碼正確也被擋**（鎖定中）。"""
    await _seed_user(db_session)
    for _ in range(5):
        resp = await client.post(
            "/api/v1/auth/login", json={"username": "manager1", "password": "wrong"}
        )
        assert resp.status_code == 401
    locked = await client.post(
        "/api/v1/auth/login", json={"username": "manager1", "password": "pw-123456"}
    )
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0


async def test_throttled_request_does_no_hash_work(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """節流中的請求在進入 argon2 驗證**之前**就被擋（防 CPU 耗盡）。"""
    await _seed_user(db_session)
    for _ in range(5):
        await client.post("/api/v1/auth/login", json={"username": "manager1", "password": "x"})

    calls: list[str] = []
    original = UserService.authenticate

    async def _spy(self: UserService, username: str, password: str) -> User | None:
        calls.append(username)
        return await original(self, username, password)

    monkeypatch.setattr(UserService, "authenticate", _spy)
    locked = await client.post(
        "/api/v1/auth/login", json={"username": "manager1", "password": "pw-123456"}
    )
    assert locked.status_code == 429
    assert calls == []  # 未進入認證（也就未做任何雜湊運算）


async def test_login_success_resets_failure_count(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await _seed_user(db_session)
    for _ in range(4):
        await client.post("/api/v1/auth/login", json={"username": "manager1", "password": "x"})
    ok = await client.post(
        "/api/v1/auth/login", json={"username": "manager1", "password": "pw-123456"}
    )
    assert ok.status_code == 200  # 第 5 次（正確）仍可，且成功重置帳號失敗計數
    again = await client.post(
        "/api/v1/auth/login", json={"username": "manager1", "password": "pw-123456"}
    )
    assert again.status_code == 200
