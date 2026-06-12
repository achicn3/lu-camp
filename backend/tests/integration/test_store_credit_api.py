"""購物金 API 整合測試（SC-1：餘額/歷史查詢＋人工校正；§11 合約形狀）。"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import StoreCreditSourceType, UserRole


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


async def _seed(session: AsyncSession) -> tuple[int, int, int, str, str]:
    """回 (store_id, manager_id, member_id, manager_token, clerk_token)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    manager = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    member = Contact(store_id=store.id, name="會員甲", roles=["MEMBER"])
    session.add_all([manager, clerk, member])
    await session.flush()
    mgr_token = encode_access_token(user_id=manager.id, role="MANAGER", store_id=store.id)
    clk_token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return store.id, manager.id, member.id, mgr_token, clk_token


def _auth(token: str, *, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


async def test_get_balance_and_history(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    store_id, manager_id, member_id, mgr_token, _ = await _seed(db_session)
    svc = StoreCreditService(db_session)
    await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(1000),
        premium_rate=Decimal("0.1000"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=1,
        created_by=manager_id,
    )
    resp = await client.get(f"/api/v1/contacts/{member_id}/store-credit", headers=_auth(mgr_token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["balance"] == "1100"  # 金額字串（§6/§11）
    assert len(body["entries"]) == 1
    entry = body["entries"][0]
    assert entry["entry_type"] == "CREDIT"
    assert entry["signed_amount"] == "1100"
    assert entry["cash_equivalent"] == "1000"
    assert entry["premium_rate_applied"] == "0.1000"


async def test_balance_zero_for_unknown_account(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, _manager_id, member_id, mgr_token, _ = await _seed(db_session)
    resp = await client.get(f"/api/v1/contacts/{member_id}/store-credit", headers=_auth(mgr_token))
    assert resp.status_code == 200
    assert resp.json()["balance"] == "0"
    assert resp.json()["entries"] == []


async def test_adjust_requires_manager(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _store_id, _manager_id, member_id, _mgr, clk_token = await _seed(db_session)
    resp = await client.post(
        f"/api/v1/contacts/{member_id}/store-credit/adjustments",
        json={"amount": "100", "reason": "測試"},
        headers=_auth(clk_token, idem="k-403"),
    )
    assert resp.status_code == 403


async def test_adjust_happy_path_and_negative_guard(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, _manager_id, member_id, mgr_token, _ = await _seed(db_session)
    ok = await client.post(
        f"/api/v1/contacts/{member_id}/store-credit/adjustments",
        json={"amount": "150", "reason": "開幕補發"},
        headers=_auth(mgr_token, idem="k-150"),
    )
    assert ok.status_code == 201
    assert ok.json()["signed_amount"] == "150"
    assert ok.json()["reason"] == "開幕補發"
    over = await client.post(
        f"/api/v1/contacts/{member_id}/store-credit/adjustments",
        json={"amount": "-500", "reason": "回收"},
        headers=_auth(mgr_token, idem="k-500"),
    )
    assert over.status_code == 409  # 餘額 150 扣 500 → 永不負


async def test_adjust_non_member_422(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    store_id, _manager_id, _member_id, mgr_token, _ = await _seed(db_session)
    outsider = Contact(store_id=store_id, name="散客", roles=[])
    db_session.add(outsider)
    await db_session.flush()
    resp = await client.post(
        f"/api/v1/contacts/{outsider.id}/store-credit/adjustments",
        json={"amount": "100", "reason": "x"},
        headers=_auth(mgr_token, idem="k-nm"),
    )
    assert resp.status_code == 422


async def test_adjust_requires_idempotency_key(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """缺 Idempotency-Key → 422（重試/雙擊不得重複改負債）。"""
    _store_id, _manager_id, member_id, mgr_token, _ = await _seed(db_session)
    resp = await client.post(
        f"/api/v1/contacts/{member_id}/store-credit/adjustments",
        json={"amount": "100", "reason": "x"},
        headers=_auth(mgr_token),
    )
    assert resp.status_code == 422


async def test_adjust_retry_same_key_returns_original(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _store_id, _manager_id, member_id, mgr_token, _ = await _seed(db_session)
    first = await client.post(
        f"/api/v1/contacts/{member_id}/store-credit/adjustments",
        json={"amount": "100", "reason": "補發"},
        headers=_auth(mgr_token, idem="retry-key"),
    )
    retry = await client.post(
        f"/api/v1/contacts/{member_id}/store-credit/adjustments",
        json={"amount": "100", "reason": "補發"},
        headers=_auth(mgr_token, idem="retry-key"),
    )
    assert first.status_code == 201
    assert retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]
    balance = await client.get(
        f"/api/v1/contacts/{member_id}/store-credit", headers=_auth(mgr_token)
    )
    assert balance.json()["balance"] == "100"  # 只加一次


async def test_endpoints_require_auth(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/contacts/1/store-credit")).status_code == 401
    assert (
        await client.post(
            "/api/v1/contacts/1/store-credit/adjustments",
            json={"amount": "1", "reason": "x"},
            headers={"Idempotency-Key": "k"},
        )
    ).status_code == 401
