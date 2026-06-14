"""SC-5a 溢價率設定＋留痕測試（docs/16 §1.3/§1.5/§6.1）。"""

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


async def _seed(session: AsyncSession) -> tuple[str, str]:
    """建店＋MANAGER＋CLERK，回 (mgr_token, clerk_token)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add_all([mgr, clerk])
    await session.flush()
    return (
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_get_settings_has_premium_fields(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk = await _seed(db_session)
    body = (await client.get("/api/v1/settings", headers=_auth(mgr))).json()
    assert body["premium_rate"] == "0.1000"
    assert body["premium_rate_min"] == "0.0000"
    assert body["premium_rate_max"] == "0.2000"
    assert body["monthly_fixed_cash_outflow"] == "0"
    assert "window_weights" in body["store_credit_engine_params"]


async def test_change_premium_writes_history(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk = await _seed(db_session)
    resp = await client.patch(
        "/api/v1/settings",
        json={"premium_rate": "0.1500", "premium_change_reason": "旺季調高"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["premium_rate"] == "0.1500"
    hist = (await client.get("/api/v1/settings/premium-rate/history", headers=_auth(mgr))).json()
    assert len(hist) == 1
    assert hist[0]["old_rate"] == "0.1000"
    assert hist[0]["new_rate"] == "0.1500"
    assert hist[0]["reason"] == "旺季調高"


async def test_premium_above_hard_cap_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk = await _seed(db_session)
    # 0.30 > 政策硬界線 0.20 → schema 擋（422）
    resp = await client.patch(
        "/api/v1/settings", json={"premium_rate": "0.3000"}, headers=_auth(mgr)
    )
    assert resp.status_code == 422


async def test_premium_outside_minmax_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk = await _seed(db_session)
    # 把上限壓到 0.05，但 premium 仍 0.10 → service 動態驗證擋（422）
    resp = await client.patch(
        "/api/v1/settings", json={"premium_rate_max": "0.0500"}, headers=_auth(mgr)
    )
    assert resp.status_code == 422
    assert "premium" in resp.json()["detail"].lower() or "溢價" in resp.json()["detail"]


async def test_min_greater_than_max_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk = await _seed(db_session)
    resp = await client.patch(
        "/api/v1/settings",
        json={"premium_rate_min": "0.1500", "premium_rate_max": "0.0500"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 422
    assert "下限" in resp.json()["detail"]


async def test_set_bounds_then_premium_within(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk = await _seed(db_session)
    # 同一 PATCH 一併調界線與 premium：min=0.10、max=0.18、premium=0.12 → 合法
    resp = await client.patch(
        "/api/v1/settings",
        json={
            "premium_rate_min": "0.1000",
            "premium_rate_max": "0.1800",
            "premium_rate": "0.1200",
        },
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text


async def test_monthly_outflow_whole_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk = await _seed(db_session)
    bad = await client.patch(
        "/api/v1/settings",
        json={"monthly_fixed_cash_outflow": "1000.5"},
        headers=_auth(mgr),
    )
    assert bad.status_code == 422
    ok = await client.patch(
        "/api/v1/settings",
        json={"monthly_fixed_cash_outflow": "30000"},
        headers=_auth(mgr),
    )
    assert ok.status_code == 200
    assert ok.json()["monthly_fixed_cash_outflow"] == "30000"
    # 超出 Numeric(12,0)（13 位數）→ 422，不可溢位 500（Codex P2 r2）
    overflow = await client.patch(
        "/api/v1/settings",
        json={"monthly_fixed_cash_outflow": "1000000000000"},
        headers=_auth(mgr),
    )
    assert overflow.status_code == 422


async def test_explicit_null_is_ignored_not_500(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """明確傳 null 視為不更動，不可 500（Codex SC-5a P2）。"""
    mgr, _clerk = await _seed(db_session)
    resp = await client.patch(
        "/api/v1/settings",
        json={"premium_rate": None, "monthly_fixed_cash_outflow": None},
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["premium_rate"] == "0.1000"  # 未更動


async def test_rate_more_than_4dp_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """溢價率超過四位小數 → 422（與 DB Numeric(5,4) 一致；Codex SC-5a P2）。"""
    mgr, _clerk = await _seed(db_session)
    resp = await client.patch(
        "/api/v1/settings", json={"premium_rate": "0.12345"}, headers=_auth(mgr)
    )
    assert resp.status_code == 422


async def test_premium_history_manager_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _mgr, clerk = await _seed(db_session)
    resp = await client.get("/api/v1/settings/premium-rate/history", headers=_auth(clerk))
    assert resp.status_code == 403
