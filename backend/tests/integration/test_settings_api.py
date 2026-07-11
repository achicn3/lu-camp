"""settings API 整合測試（GET/PATCH 端點、MANAGER 權限、範圍驗證；§11 合約形狀）。"""

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _seed_user(session: AsyncSession, role: UserRole) -> str:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    user = User(store_id=store.id, username=f"u{role.value}", password_hash="h", role=role)
    session.add(user)
    await session.flush()
    return encode_access_token(user_id=user.id, role=role.value, store_id=store.id)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_get_returns_defaults(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token = await _seed_user(db_session, UserRole.CLERK)
    resp = await client.get("/api/v1/settings", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["einvoice_enabled"] is False
    assert body["tax_rate"] == "0.05"  # 字串傳輸（§11）
    assert body["default_commission_pct"] == 50
    assert body["default_margin_pct"] == 45
    assert body["allow_clerk_manage_categories"] is False


async def test_manager_patch_updates_and_persists(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = await _seed_user(db_session, UserRole.MANAGER)
    patch = await client.patch(
        "/api/v1/settings",
        json={
            "default_margin_pct": 30,
            "allow_clerk_manage_categories": True,
        },
        headers=_auth(token),
    )
    assert patch.status_code == 200
    assert patch.json()["default_margin_pct"] == 30
    assert patch.json()["allow_clerk_manage_categories"] is True
    # 再 GET 應反映已持久化的變更。
    got = await client.get("/api/v1/settings", headers=_auth(token))
    assert got.json()["default_margin_pct"] == 30
    assert got.json()["allow_clerk_manage_categories"] is True
    # 未動到的維持預設。
    assert got.json()["default_commission_pct"] == 50
    assert got.json()["einvoice_enabled"] is False


async def test_enable_einvoice_returns_409_until_ready(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AMEGO_APP_KEY 未設定（docs/24）時，開啟 einvoice_enabled → 409、不持久化。

    以 monkeypatch 固定「未設定」情境——開發機 .env 可能已注入測試金鑰。
    """
    from app.core.config import get_settings as get_app_settings

    monkeypatch.setattr(get_app_settings(), "amego_app_key", "")
    token = await _seed_user(db_session, UserRole.MANAGER)
    # 先固定種子（savepoint 釋放；外層交易仍會整批回滾）：409 路徑的 router rollback
    # 只回滾該請求的變更，不吃掉種子 user，後續 GET 才能以同 token 驗證未持久化。
    await db_session.commit()
    resp = await client.patch(
        "/api/v1/settings", json={"einvoice_enabled": True}, headers=_auth(token)
    )
    assert resp.status_code == 409
    got = await client.get("/api/v1/settings", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["einvoice_enabled"] is False


async def test_clerk_patch_forbidden(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token = await _seed_user(db_session, UserRole.CLERK)
    resp = await client.patch(
        "/api/v1/settings", json={"einvoice_enabled": True}, headers=_auth(token)
    )
    assert resp.status_code == 403


async def test_patch_out_of_range_returns_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = await _seed_user(db_session, UserRole.MANAGER)
    resp = await client.patch(
        "/api/v1/settings", json={"default_commission_pct": 101}, headers=_auth(token)
    )
    assert resp.status_code == 422


async def test_get_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/settings")
    assert resp.status_code == 401
