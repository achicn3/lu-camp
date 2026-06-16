"""F6 A1：品牌/型號端點整合測試（收購頁 combobox：查無即建、autocomplete、跨店隔離）。"""

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


async def _store_token(session: AsyncSession, name: str) -> tuple[int, str]:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    user = User(store_id=store.id, username=f"u{store.id}", password_hash="h", role=UserRole.CLERK)
    session.add(user)
    await session.flush()
    return store.id, encode_access_token(user_id=user.id, role="CLERK", store_id=store.id)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_brand_create_list_and_dedup(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, token = await _store_token(db_session, "店A")
    assert (await client.get("/api/v1/brands", headers=_auth(token))).json() == []

    created = await client.post("/api/v1/brands", json={"name": "Patagonia"}, headers=_auth(token))
    assert created.status_code == 200, created.text
    brand_id = created.json()["id"]
    # 同名再建 → get_or_create 回同一筆（冪等、不重複）
    again = await client.post("/api/v1/brands", json={"name": "Patagonia"}, headers=_auth(token))
    assert again.json()["id"] == brand_id

    listed = (await client.get("/api/v1/brands", headers=_auth(token))).json()
    assert [b["name"] for b in listed] == ["Patagonia"]
    # 空白名稱被擋
    assert (
        await client.post("/api/v1/brands", json={"name": "   "}, headers=_auth(token))
    ).status_code == 422


async def test_brand_autocomplete_q(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _store_id, token = await _store_token(db_session, "店A")
    for name in ("Arc'teryx", "Patagonia", "Mammut"):
        await client.post("/api/v1/brands", json={"name": name}, headers=_auth(token))
    hits = (await client.get("/api/v1/brands?q=mam", headers=_auth(token))).json()
    assert [b["name"] for b in hits] == ["Mammut"]


async def test_product_model_create_filter_and_brand_scope(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, token = await _store_token(db_session, "店A")

    async def _brand(name: str) -> int:
        resp = await client.post("/api/v1/brands", json={"name": name}, headers=_auth(token))
        return int(resp.json()["id"])

    b1 = await _brand("B1")
    b2 = await _brand("B2")

    m1 = await client.post(
        "/api/v1/product-models", json={"brand_id": b1, "name": "Model-X"}, headers=_auth(token)
    )
    assert m1.status_code == 200, m1.text
    # 同品牌同名 → 去重
    m1b = await client.post(
        "/api/v1/product-models", json={"brand_id": b1, "name": "Model-X"}, headers=_auth(token)
    )
    assert m1b.json()["id"] == m1.json()["id"]
    # 不同品牌同名 → 視為不同型號
    m2 = await client.post(
        "/api/v1/product-models", json={"brand_id": b2, "name": "Model-X"}, headers=_auth(token)
    )
    assert m2.json()["id"] != m1.json()["id"]

    # brand_id 篩選只回該品牌型號
    only_b1 = (
        await client.get(f"/api/v1/product-models?brand_id={b1}", headers=_auth(token))
    ).json()
    assert [m["id"] for m in only_b1] == [m1.json()["id"]]


async def test_product_model_unknown_brand_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, token = await _store_token(db_session, "店A")
    resp = await client.post(
        "/api/v1/product-models", json={"brand_id": 99999, "name": "X"}, headers=_auth(token)
    )
    assert resp.status_code == 404


async def test_cross_store_isolation(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _a, token_a = await _store_token(db_session, "店A")
    _b, token_b = await _store_token(db_session, "店B")
    brand_a = (
        await client.post("/api/v1/brands", json={"name": "OnlyA"}, headers=_auth(token_a))
    ).json()["id"]
    # 店B 看不到店A 的品牌
    assert (await client.get("/api/v1/brands", headers=_auth(token_b))).json() == []
    # 店B 不能在店A 的品牌下建型號
    resp = await client.post(
        "/api/v1/product-models", json={"brand_id": brand_a, "name": "X"}, headers=_auth(token_b)
    )
    assert resp.status_code == 404
