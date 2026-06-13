"""acquisition API 整合測試（端到端：router→service→repository，含 §11 合約形狀）。

驗證收購成功回可辨識的 acquisition_id + 待印識別碼；失敗回清楚、可辨識的 HTTP 錯誤
（缺 national_id / 未開帳 / 形狀錯誤 / 抽成越界），供前端顯示與安全重做。
"""

import itertools
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
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


async def _seed_token(session: AsyncSession) -> tuple[int, str]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    return store.id, encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)


_idem_counter = itertools.count()


def _auth(token: str) -> dict[str, str]:
    """帶認證＋自動唯一冪等鍵（收購端點必帶 Idempotency-Key；各呼叫不互撞）。"""
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": f"test-key-{next(_idem_counter)}",
    }


async def _open_drawer(client: httpx.AsyncClient, token: str) -> None:
    resp = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    assert resp.status_code == 201


async def _make_seller(client: httpx.AsyncClient, token: str, *, with_id: bool = True) -> int:
    body: dict[str, object] = {"name": "賣家", "roles": ["SELLER" if with_id else "MEMBER"]}
    if with_id:
        body["national_id"] = "A123456789"
    resp = await client.post("/api/v1/contacts", json=body, headers=_auth(token))
    assert resp.status_code == 201
    return int(resp.json()["id"])


async def test_buyout_happy_path(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, token = await _seed_token(db_session)
    await _open_drawer(client, token)
    contact_id = await _make_seller(client, token)

    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BUYOUT",
            "contact_id": contact_id,
            "items": [
                {"name": "相機", "grade": "A", "listed_price": "3000", "acquisition_cost": "1800"}
            ],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["acquisition_id"] > 0
    assert len(body["item_codes"]) == 1
    assert body["item_codes"][0].startswith("S")
    assert body["total_cash_paid"] == "1800"  # 字串傳輸
    assert body["lot_code"] is None

    got = await client.get(f"/api/v1/acquisitions/{body['acquisition_id']}", headers=_auth(token))
    assert got.status_code == 200
    assert got.json()["type"] == "BUYOUT"


async def test_acquisition_audit_has_no_national_id_plaintext(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """端到端：賣方有加密 national_id；收購稽核絕不出現其明文。"""
    _, token = await _seed_token(db_session)
    await _open_drawer(client, token)
    contact_id = await _make_seller(client, token)  # national_id = A123456789（加密儲存）

    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BUYOUT",
            "contact_id": contact_id,
            "items": [
                {"name": "相機", "grade": "A", "listed_price": "3000", "acquisition_cost": "1800"}
            ],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201

    entry = await db_session.scalar(select(AuditLog).where(AuditLog.action == "CREATE_ACQUISITION"))
    assert entry is not None
    assert "A123456789" not in str(entry.before)
    assert "A123456789" not in str(entry.after)
    assert entry.after is not None and entry.after["contact_id"] == contact_id


async def test_bulk_lot_happy_path(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, token = await _seed_token(db_session)
    await _open_drawer(client, token)
    contact_id = await _make_seller(client, token)

    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BULK_LOT",
            "contact_id": contact_id,
            "lot": {
                "name": "散裝A堆",
                "acquisition_cost": "3000",
                "acquisition_basis": "WEIGHT",
                "total_qty": 50,
                "unit_price": "100",
            },
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["lot_code"].startswith("L")
    assert body["item_codes"] == []
    assert body["total_cash_paid"] == "3000"


async def test_missing_national_id_returns_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, token = await _seed_token(db_session)
    await _open_drawer(client, token)
    contact_id = await _make_seller(client, token, with_id=False)  # 純會員、無 national_id
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BUYOUT",
            "contact_id": contact_id,
            "items": [{"name": "x", "grade": "A", "listed_price": "100", "acquisition_cost": "50"}],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422
    assert "national_id" in resp.json()["detail"]


async def test_buyout_without_open_drawer_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, token = await _seed_token(db_session)
    contact_id = await _make_seller(client, token)  # 未開帳
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BUYOUT",
            "contact_id": contact_id,
            "items": [{"name": "x", "grade": "A", "listed_price": "100", "acquisition_cost": "50"}],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 409


async def test_bad_shape_returns_422(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, token = await _seed_token(db_session)
    await _open_drawer(client, token)
    contact_id = await _make_seller(client, token)
    # BULK_LOT 卻給 items → schema 擋下。
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BULK_LOT",
            "contact_id": contact_id,
            "items": [{"name": "x", "grade": "A", "listed_price": "100", "acquisition_cost": "50"}],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422


async def test_commission_out_of_range_returns_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, token = await _seed_token(db_session)
    contact_id = await _make_seller(client, token)
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "CONSIGNMENT",
            "contact_id": contact_id,
            "items": [{"name": "x", "grade": "A", "listed_price": "100", "commission_pct": 200}],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422


async def test_get_acquisition_404(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, token = await _seed_token(db_session)
    resp = await client.get("/api/v1/acquisitions/999999", headers=_auth(token))
    assert resp.status_code == 404


async def test_requires_auth(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    resp = await client.post("/api/v1/acquisitions", json={})
    assert resp.status_code == 401


async def test_unexpected_error_rolls_back(
    client: httpx.AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """非預期錯誤（付現步驟炸掉）也整筆 rollback；router 不吞例外。"""
    _, token = await _seed_token(db_session)
    await _open_drawer(client, token)
    contact_id = await _make_seller(client, token)

    async def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("付現失敗")

    monkeypatch.setattr(CashDrawerService, "record_movement", _boom)
    with pytest.raises(RuntimeError):
        await client.post(
            "/api/v1/acquisitions",
            json={
                "type": "BUYOUT",
                "contact_id": contact_id,
                "items": [
                    {"name": "x", "grade": "A", "listed_price": "100", "acquisition_cost": "50"}
                ],
            },
            headers=_auth(token),
        )
