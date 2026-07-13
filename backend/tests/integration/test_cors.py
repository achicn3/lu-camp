"""CORS 整合測試：瀏覽器前端（不同埠）呼叫 API 必須帶回允許來源標頭。

實跑發現（2026-06-12）：未掛 CORSMiddleware 時，LAN 前端（docs/10 架構）
所有跨埠請求被瀏覽器擋下、登入直接「無法連線」。
"""

from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.main import create_app


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


async def test_preflight_allows_configured_origin(client: httpx.AsyncClient) -> None:
    resp = await client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_unknown_origin_not_allowed(client: httpx.AsyncClient) -> None:
    resp = await client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in resp.headers


async def test_error_code_header_is_exposed_to_browser(client: httpx.AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/health",
        headers={"Origin": "http://localhost:3000"},
    )

    exposed = resp.headers.get("access-control-expose-headers", "").lower().split(", ")
    assert "x-lu-camp-error-code" in exposed
