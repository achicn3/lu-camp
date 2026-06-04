"""cashdrawer API 整合測試（open/current/movements/close 端點，§11 合約形狀）。"""

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


async def _seed_token(session: AsyncSession) -> str:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    return encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_open_current_movement_close_flow(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = await _seed_token(db_session)

    opened = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    assert opened.status_code == 201
    session_id = opened.json()["id"]
    assert opened.json()["status"] == "OPEN"
    assert opened.json()["opening_float"] == "1000"

    current = await client.get("/api/v1/cash-sessions/current", headers=_auth(token))
    assert current.status_code == 200
    assert current.json()["id"] == session_id

    # 手動現金調整（可正可負）。
    adj = await client.post(
        f"/api/v1/cash-sessions/{session_id}/movements",
        json={"type": "MANUAL_ADJUST", "amount": "-50"},
        headers=_auth(token),
    )
    assert adj.status_code == 201
    assert adj.json()["amount"] == "-50"

    closed = await client.post(
        f"/api/v1/cash-sessions/{session_id}/close",
        json={"counted_amount": "900"},
        headers=_auth(token),
    )
    assert closed.status_code == 200
    body = closed.json()
    assert body["status"] == "CLOSED"
    assert body["expected_amount"] == "950"  # 1000 - 50
    assert body["variance"] == "-50"  # 900 - 950


async def test_open_twice_returns_409(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token = await _seed_token(db_session)
    first = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    assert first.status_code == 201
    again = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "500"}, headers=_auth(token)
    )
    assert again.status_code == 409


async def test_current_returns_null_when_none(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = await _seed_token(db_session)
    resp = await client.get("/api/v1/cash-sessions/current", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json() is None


async def test_non_manual_negative_amount_returns_400(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = await _seed_token(db_session)
    opened = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    session_id = opened.json()["id"]
    resp = await client.post(
        f"/api/v1/cash-sessions/{session_id}/movements",
        json={"type": "SALE_IN", "amount": "-10"},
        headers=_auth(token),
    )
    assert resp.status_code == 400


async def test_movement_on_wrong_session_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = await _seed_token(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    resp = await client.post(
        "/api/v1/cash-sessions/999999/movements",
        json={"type": "MANUAL_ADJUST", "amount": "10"},
        headers=_auth(token),
    )
    assert resp.status_code == 409


async def test_close_not_found_returns_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = await _seed_token(db_session)
    resp = await client.post(
        "/api/v1/cash-sessions/999999/close",
        json={"counted_amount": "0"},
        headers=_auth(token),
    )
    assert resp.status_code == 404


async def test_negative_opening_float_returns_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = await _seed_token(db_session)
    resp = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "-1"}, headers=_auth(token)
    )
    assert resp.status_code == 422


async def test_reclose_returns_409(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    token = await _seed_token(db_session)
    opened = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    session_id = opened.json()["id"]
    first = await client.post(
        f"/api/v1/cash-sessions/{session_id}/close",
        json={"counted_amount": "1000"},
        headers=_auth(token),
    )
    assert first.status_code == 200
    again = await client.post(
        f"/api/v1/cash-sessions/{session_id}/close",
        json={"counted_amount": "1000"},
        headers=_auth(token),
    )
    assert again.status_code == 409
