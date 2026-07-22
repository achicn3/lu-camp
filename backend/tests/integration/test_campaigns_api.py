"""C1 門市活動 API 整合測試（docs/21）：

CRUD + 狀態機（DRAFT→ACTIVE→ENDED、→CANCELLED）、單一 ACTIVE 守衛、折扣/區間驗證、
跨店隔離、MANAGER 限定、稽核留痕。
"""

from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
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


async def _seed(session: AsyncSession) -> tuple[str, str, int]:
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
        store.id,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "開幕九折",
        "discount_pct": 10,
        "starts_at": "2026-06-01T00:00:00Z",
        "ends_at": "2026-07-01T00:00:00Z",
    }
    base.update(overrides)
    return base


async def _create(client: httpx.AsyncClient, mgr: str, **overrides: object) -> dict[str, object]:
    resp = await client.post("/api/v1/campaigns", json=_payload(**overrides), headers=_auth(mgr))
    assert resp.status_code == 201, resp.text
    body: dict[str, object] = resp.json()
    return body


async def test_create_campaign_defaults(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store = await _seed(db_session)
    body = await _create(client, mgr)
    assert body["status"] == "DRAFT"
    assert body["discount_pct"] == 10
    # 預設：自有序號+自有散裝開；catalog/寄售關（docs/21 §8）
    assert body["applies_owned_serialized"] is True
    assert body["applies_owned_bulk"] is True
    assert body["applies_catalog"] is False
    assert body["applies_consignment"] is False


async def test_create_invalid_discount_pct(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store = await _seed(db_session)
    for bad in (0, 100, 150):
        resp = await client.post(
            "/api/v1/campaigns", json=_payload(discount_pct=bad), headers=_auth(mgr)
        )
        assert resp.status_code == 422, f"pct={bad}: {resp.text}"


async def test_create_bad_window(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _clerk, _store = await _seed(db_session)
    resp = await client.post(
        "/api/v1/campaigns",
        json=_payload(starts_at="2026-07-01T00:00:00Z", ends_at="2026-06-01T00:00:00Z"),
        headers=_auth(mgr),
    )
    assert resp.status_code == 409


async def test_create_rejects_datetime_without_offset(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store = await _seed(db_session)
    resp = await client.post(
        "/api/v1/campaigns",
        json=_payload(
            starts_at="2026-07-01T00:00:00",
            ends_at="2026-07-02T00:00:00",
        ),
        headers=_auth(mgr),
    )

    assert resp.status_code == 422, resp.text


async def test_activate_and_single_active_guard(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store = await _seed(db_session)
    a = await _create(client, mgr, name="活動A")
    b = await _create(client, mgr, name="活動B")
    act_a = await client.post(f"/api/v1/campaigns/{a['id']}/activate", headers=_auth(mgr))
    assert act_a.status_code == 200
    assert act_a.json()["status"] == "ACTIVE"
    # 第二個啟用 → 同店已有 ACTIVE → 409
    act_b = await client.post(f"/api/v1/campaigns/{b['id']}/activate", headers=_auth(mgr))
    assert act_b.status_code == 409


async def test_end_then_can_activate_another(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store = await _seed(db_session)
    a = await _create(client, mgr, name="活動A")
    b = await _create(client, mgr, name="活動B")
    await client.post(f"/api/v1/campaigns/{a['id']}/activate", headers=_auth(mgr))
    ended = await client.post(f"/api/v1/campaigns/{a['id']}/end", headers=_auth(mgr))
    assert ended.status_code == 200
    assert ended.json()["status"] == "ENDED"
    # A 結束後 B 可啟用
    act_b = await client.post(f"/api/v1/campaigns/{b['id']}/activate", headers=_auth(mgr))
    assert act_b.status_code == 200


async def test_illegal_transitions(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _clerk, _store = await _seed(db_session)
    a = await _create(client, mgr)
    # 結束尚未啟用的活動 → 409
    bad_end = await client.post(f"/api/v1/campaigns/{a['id']}/end", headers=_auth(mgr))
    assert bad_end.status_code == 409
    # 啟用後不可再啟用 → 409
    await client.post(f"/api/v1/campaigns/{a['id']}/activate", headers=_auth(mgr))
    again = await client.post(f"/api/v1/campaigns/{a['id']}/activate", headers=_auth(mgr))
    assert again.status_code == 409


async def test_cancel_draft_and_active(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _clerk, _store = await _seed(db_session)
    a = await _create(client, mgr, name="A")
    cancel_a = await client.post(f"/api/v1/campaigns/{a['id']}/cancel", headers=_auth(mgr))
    assert cancel_a.status_code == 200
    assert cancel_a.json()["status"] == "CANCELLED"
    b = await _create(client, mgr, name="B")
    await client.post(f"/api/v1/campaigns/{b['id']}/activate", headers=_auth(mgr))
    cancel_b = await client.post(f"/api/v1/campaigns/{b['id']}/cancel", headers=_auth(mgr))
    assert cancel_b.status_code == 200
    assert cancel_b.json()["status"] == "CANCELLED"


async def test_list_and_filter(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _clerk, _store = await _seed(db_session)
    a = await _create(client, mgr, name="A")
    await _create(client, mgr, name="B")
    await client.post(f"/api/v1/campaigns/{a['id']}/activate", headers=_auth(mgr))
    all_resp = await client.get("/api/v1/campaigns", headers=_auth(mgr))
    assert len(all_resp.json()) == 2
    active_resp = await client.get(
        "/api/v1/campaigns", params={"status": "ACTIVE"}, headers=_auth(mgr)
    )
    rows = active_resp.json()
    assert len(rows) == 1 and rows[0]["name"] == "A"


async def test_consignment_toggle_configurable(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """寄售折扣為可設定開關（docs/21 §8.1）：可開啟（開啟後一律按比例分攤、無 bearing 選項）。"""
    mgr, _clerk, _store = await _seed(db_session)
    body = await _create(client, mgr, name="週年慶", applies_consignment=True)
    assert body["applies_consignment"] is True
    assert "consignment_discount_bearing" not in body


async def test_cross_store_isolation(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _clerk, _store = await _seed(db_session)
    other = Store(name="他店")
    db_session.add(other)
    await db_session.flush()
    other_mgr = User(store_id=other.id, username="om", password_hash="h", role=UserRole.MANAGER)
    db_session.add(other_mgr)
    await db_session.flush()
    other_token = encode_access_token(user_id=other_mgr.id, role="MANAGER", store_id=other.id)
    a = await _create(client, mgr, name="本店活動")
    # 他店看不到、改不到
    assert (
        await client.get(f"/api/v1/campaigns/{a['id']}", headers=_auth(other_token))
    ).status_code == 404
    assert (
        await client.post(f"/api/v1/campaigns/{a['id']}/activate", headers=_auth(other_token))
    ).status_code == 404
    assert (await client.get("/api/v1/campaigns", headers=_auth(other_token))).json() == []


async def test_manager_only(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _mgr, clerk, _store = await _seed(db_session)
    resp = await client.post("/api/v1/campaigns", json=_payload(), headers=_auth(clerk))
    assert resp.status_code == 403
    list_resp = await client.get("/api/v1/campaigns", headers=_auth(clerk))
    assert list_resp.status_code == 403


async def test_audit_written_on_lifecycle(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, store_id = await _seed(db_session)
    a = await _create(client, mgr)
    await client.post(f"/api/v1/campaigns/{a['id']}/activate", headers=_auth(mgr))
    await client.post(f"/api/v1/campaigns/{a['id']}/end", headers=_auth(mgr))
    actions = (
        (
            await db_session.execute(
                select(AuditLog.action).where(
                    AuditLog.store_id == store_id, AuditLog.entity_type == "campaign"
                )
            )
        )
        .scalars()
        .all()
    )
    assert "CAMPAIGN_CREATE" in actions
    assert "CAMPAIGN_ACTIVATE" in actions
    assert "CAMPAIGN_END" in actions


async def test_get_missing_404(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _clerk, _store = await _seed(db_session)
    resp = await client.get("/api/v1/campaigns/99999", headers=_auth(mgr))
    assert resp.status_code == 404


async def test_campaigns_create_is_persisted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store = await _seed(db_session)
    await _create(client, mgr)
    from app.modules.campaigns.models import Campaign

    count = await db_session.scalar(select(func.count()).select_from(Campaign))
    assert count == 1
