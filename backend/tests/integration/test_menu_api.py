"""menu API 整合測試：餐飲菜單品項 CRUD、RBAC、去重、改價稽核、封存、store 隔離。"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.menu.models import MenuItem
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


async def _seed(session: AsyncSession) -> tuple[str, str, int]:
    """建店+店員+經理，回 (clerk_token, manager_token, store_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    session.add_all([clerk, mgr])
    await session.flush()
    return (
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        store.id,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_create_list_menu_item(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, mgr, _ = await _seed(db_session)
    resp = await client.post(
        "/api/v1/menu-items",
        json={"name": "手沖-耶加", "unit_price": "180", "category": "咖啡"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "手沖-耶加"
    assert body["unit_price"] == "180"  # 字串傳輸
    assert body["is_available"] is True

    listed = await client.get("/api/v1/menu-items", headers=_auth(mgr))
    assert listed.status_code == 200
    assert [i["name"] for i in listed.json()] == ["手沖-耶加"]


async def test_duplicate_name_409(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, mgr, _ = await _seed(db_session)
    payload = {"name": "拿鐵", "unit_price": "150"}
    first = await client.post("/api/v1/menu-items", json=payload, headers=_auth(mgr))
    assert first.status_code == 201
    dup = await client.post("/api/v1/menu-items", json=payload, headers=_auth(mgr))
    assert dup.status_code == 409


async def test_invalid_price_422(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, mgr, _ = await _seed(db_session)
    # 0 元（gt=0 schema 擋）→ 422
    resp = await client.post(
        "/api/v1/menu-items", json={"name": "贈品", "unit_price": "0"}, headers=_auth(mgr)
    )
    assert resp.status_code == 422


async def test_clerk_cannot_write_but_can_list(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, mgr, _ = await _seed(db_session)
    await client.post(
        "/api/v1/menu-items", json={"name": "美式", "unit_price": "120"}, headers=_auth(mgr)
    )
    # 店員可讀（POS 取菜單）
    listed = await client.get("/api/v1/menu-items", headers=_auth(clerk))
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    # 店員不可建
    denied = await client.post(
        "/api/v1/menu-items", json={"name": "卡布", "unit_price": "140"}, headers=_auth(clerk)
    )
    assert denied.status_code == 403


async def test_update_price_writes_audit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr, _ = await _seed(db_session)
    created = await client.post(
        "/api/v1/menu-items", json={"name": "卡布奇諾", "unit_price": "150"}, headers=_auth(mgr)
    )
    item_id = created.json()["id"]
    upd = await client.patch(
        f"/api/v1/menu-items/{item_id}", json={"unit_price": "160"}, headers=_auth(mgr)
    )
    assert upd.status_code == 200
    assert upd.json()["unit_price"] == "160"
    audits = (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "UPDATE_MENU_ITEM_PRICE")
        )
    )
    assert audits == 1


async def test_available_only_filter(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr, _ = await _seed(db_session)
    created = await client.post(
        "/api/v1/menu-items", json={"name": "季節限定", "unit_price": "200"}, headers=_auth(mgr)
    )
    item_id = created.json()["id"]
    # 下架
    await client.patch(
        f"/api/v1/menu-items/{item_id}", json={"is_available": False}, headers=_auth(mgr)
    )
    # 管理列表含停售
    full = await client.get("/api/v1/menu-items", headers=_auth(mgr))
    assert len(full.json()) == 1
    # POS 只列可售 → 空
    pos = await client.get("/api/v1/menu-items?available_only=true", headers=_auth(mgr))
    assert pos.json() == []


async def test_archive_hides_from_list(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, mgr, _ = await _seed(db_session)
    created = await client.post(
        "/api/v1/menu-items", json={"name": "已停售品", "unit_price": "100"}, headers=_auth(mgr)
    )
    item_id = created.json()["id"]
    deleted = await client.delete(f"/api/v1/menu-items/{item_id}", headers=_auth(mgr))
    assert deleted.status_code == 200
    listed = await client.get("/api/v1/menu-items", headers=_auth(mgr))
    assert listed.json() == []
    # 二次刪除 → 404（已封存）
    again = await client.delete(f"/api/v1/menu-items/{item_id}", headers=_auth(mgr))
    assert again.status_code == 404


async def test_store_isolation(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, mgr_a, _ = await _seed(db_session)
    # 另一間店（真實建檔）的品項不應出現在 A 店清單，也不可被 A 店改價（404）。
    store_b = Store(name="他店")
    db_session.add(store_b)
    await db_session.flush()
    other = MenuItem(store_id=store_b.id, name="他店品", unit_price=Decimal("99"))
    db_session.add(other)
    await db_session.flush()
    listed = await client.get("/api/v1/menu-items", headers=_auth(mgr_a))
    assert all(i["name"] != "他店品" for i in listed.json())
    resp = await client.patch(
        f"/api/v1/menu-items/{other.id}", json={"unit_price": "1"}, headers=_auth(mgr_a)
    )
    assert resp.status_code == 404
