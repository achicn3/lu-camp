"""store API 整合測試：GET /stores/{id}/receipt-header（收據抬頭、§11 合約形狀）。

端點刻意不需認證（收據抬頭為非 PII 公開資訊，見 router docstring / 產品裁示）。
"""

from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.main import create_app
from app.modules.store.models import Store


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


async def _seed_store(session: AsyncSession, **kwargs: str) -> int:
    store = Store(**kwargs)
    session.add(store)
    await session.flush()
    return store.id


async def test_receipt_header_returns_all_fields(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(
        db_session,
        name="路營二手",
        tax_id="12345678",
        address="台北市中正區",
        phone="02-1234-5678",
        invoice_track_info="AB",
    )
    resp = await client.get(f"/api/v1/stores/{store_id}/receipt-header")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "name": "路營二手",
        "tax_id": "12345678",
        "address": "台北市中正區",
        "phone": "02-1234-5678",
        "invoice_track_info": "AB",
    }


async def test_receipt_header_optional_fields_null(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """選填欄位未設 → 如實回 null（不臆造）。"""
    store_id = await _seed_store(db_session, name="只有店名")
    resp = await client.get(f"/api/v1/stores/{store_id}/receipt-header")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "只有店名"
    assert body["tax_id"] is None
    assert body["address"] is None
    assert body["phone"] is None
    assert body["invoice_track_info"] is None


async def test_receipt_header_unknown_store_returns_404(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/stores/999999/receipt-header")
    assert resp.status_code == 404


async def test_receipt_header_no_auth_required(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """刻意不需認證：不帶任何 Authorization header 也應 200。"""
    store_id = await _seed_store(db_session, name="免認證門市")
    resp = await client.get(f"/api/v1/stores/{store_id}/receipt-header")
    assert resp.status_code == 200
    assert "Authorization" not in resp.request.headers
