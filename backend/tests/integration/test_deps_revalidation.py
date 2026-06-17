"""D-4：永不過期 token 的逐請求 DB 覆核（core/deps.get_current_user）。

token 簽發後可永久有效，故認證時必須以 DB 現況為準：被停用/刪除者一律 401；
角色降權即時生效（以 DB role 判權，非 token claim）。
"""

from collections.abc import AsyncGenerator

import httpx
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


async def _seed(session: AsyncSession, role: UserRole = UserRole.MANAGER) -> User:
    store = Store(name="覆核店")
    session.add(store)
    await session.flush()
    user = User(
        store_id=store.id, username=f"u{store.id}", password_hash="h", role=role, is_active=True
    )
    session.add(user)
    await session.flush()
    return user


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_active_user_token_passes_auth(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await _seed(db_session)
    token = encode_access_token(user_id=user.id, role="MANAGER", store_id=user.store_id)
    resp = await client.get("/api/v1/cash-sessions/current", headers=_auth(token))
    assert resp.status_code != 401  # 認證通過（業務狀態碼不在此測）


async def test_deactivated_user_token_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """停用後，既發 token 立即失效（不等過期）。"""
    user = await _seed(db_session)
    token = encode_access_token(user_id=user.id, role="MANAGER", store_id=user.store_id)
    user.is_active = False
    await db_session.flush()
    resp = await client.get("/api/v1/cash-sessions/current", headers=_auth(token))
    assert resp.status_code == 401


async def test_deleted_user_token_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await _seed(db_session)
    token = encode_access_token(user_id=user.id, role="MANAGER", store_id=user.store_id)
    await db_session.delete(user)
    await db_session.flush()
    resp = await client.get("/api/v1/cash-sessions/current", headers=_auth(token))
    assert resp.status_code == 401


async def test_demotion_takes_effect_via_db_role(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """降權即時生效：即便 token claim 仍是 MANAGER，DB 改為 CLERK 後 manager 端點回 403。"""
    user = await _seed(db_session, role=UserRole.MANAGER)
    token = encode_access_token(user_id=user.id, role="MANAGER", store_id=user.store_id)
    ok = await client.get("/api/v1/reports/store-credit/liability", headers=_auth(token))
    assert ok.status_code == 200
    user.role = UserRole.CLERK
    await db_session.flush()
    demoted = await client.get("/api/v1/reports/store-credit/liability", headers=_auth(token))
    assert demoted.status_code == 403
