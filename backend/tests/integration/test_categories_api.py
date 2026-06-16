"""F6 A2/A3：分類 + 定價規則端點整合測試（建立 seed 規則、目標毛利、manager 寫入、跨店隔離）。"""

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


async def _store(session: AsyncSession, name: str) -> tuple[int, str, str]:
    """建店 + manager + clerk，回 (store_id, mgr_token, clerk_token)。"""
    store = Store(name=name)
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username=f"m{store.id}", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username=f"c{store.id}", password_hash="h", role=UserRole.CLERK)
    session.add_all([mgr, clerk])
    await session.flush()
    return (
        store.id,
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_create_category_seeds_rules_and_default_target(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _sid, mgr, _clerk = await _store(db_session, "店A")
    created = await client.post("/api/v1/categories", json={"name": "登山服飾"}, headers=_auth(mgr))
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["name"] == "登山服飾"
    assert body["target_margin_pct"] == 45  # 未給 → 店層級 default_margin_pct
    cat_id = body["id"]
    # 同名再建 → 去重
    again = await client.post("/api/v1/categories", json={"name": "登山服飾"}, headers=_auth(mgr))
    assert again.json()["id"] == cat_id
    # seed 5 個成色帶規則（S–D，不含 E）
    rules = (
        await client.get(f"/api/v1/categories/{cat_id}/pricing-rules", headers=_auth(mgr))
    ).json()
    assert sorted(r["condition_band"] for r in rules) == ["A", "B", "C", "D", "S"]
    assert all(r["discount_ceiling_pct"] == 60 for r in rules)


async def test_create_category_explicit_target(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _sid, mgr, _clerk = await _store(db_session, "店A")
    body = (
        await client.post(
            "/api/v1/categories", json={"name": "鞋類", "target_margin_pct": 55}, headers=_auth(mgr)
        )
    ).json()
    assert body["target_margin_pct"] == 55


async def test_update_target_and_rules_manager_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _sid, mgr, clerk = await _store(db_session, "店A")
    cat_id = (
        await client.post("/api/v1/categories", json={"name": "包款"}, headers=_auth(mgr))
    ).json()["id"]

    # PATCH target：manager OK、clerk 403
    assert (
        await client.patch(
            f"/api/v1/categories/{cat_id}", json={"target_margin_pct": 50}, headers=_auth(clerk)
        )
    ).status_code == 403
    patched = await client.patch(
        f"/api/v1/categories/{cat_id}", json={"target_margin_pct": 50}, headers=_auth(mgr)
    )
    assert patched.status_code == 200
    assert patched.json()["target_margin_pct"] == 50

    # PUT rules：manager 改 S 帶，clerk 403
    put_body = {"rules": [{"condition_band": "S", "discount_ceiling_pct": 30,
                           "min_margin_pct": 25, "min_price_multiple": 1.5}]}
    assert (
        await client.put(
            f"/api/v1/categories/{cat_id}/pricing-rules", json=put_body, headers=_auth(clerk)
        )
    ).status_code == 403
    put = await client.put(
        f"/api/v1/categories/{cat_id}/pricing-rules", json=put_body, headers=_auth(mgr)
    )
    assert put.status_code == 200
    s_rule = next(r for r in put.json() if r["condition_band"] == "S")
    assert s_rule["discount_ceiling_pct"] == 30 and s_rule["min_price_multiple"] == "1.5"


async def test_unknown_category_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _sid, mgr, _clerk = await _store(db_session, "店A")
    assert (
        await client.get("/api/v1/categories/99999/pricing-rules", headers=_auth(mgr))
    ).status_code == 404
    assert (
        await client.patch(
            "/api/v1/categories/99999", json={"target_margin_pct": 40}, headers=_auth(mgr)
        )
    ).status_code == 404


async def test_category_cross_store_isolation(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _a, mgr_a, _ca = await _store(db_session, "店A")
    _b, mgr_b, _cb = await _store(db_session, "店B")
    cat_a = (
        await client.post("/api/v1/categories", json={"name": "OnlyA"}, headers=_auth(mgr_a))
    ).json()["id"]
    assert (await client.get("/api/v1/categories", headers=_auth(mgr_b))).json() == []
    # 店B 不能讀/改店A 的分類規則
    assert (
        await client.get(f"/api/v1/categories/{cat_a}/pricing-rules", headers=_auth(mgr_b))
    ).status_code == 404
