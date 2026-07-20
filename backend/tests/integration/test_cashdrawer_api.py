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


async def _seed_manager(session: AsyncSession, token: str) -> str:
    """以既有 clerk token 解出 store，另建 MANAGER（手動調整限 MANAGER）。"""
    from app.core.security import decode_access_token

    store_id = int(decode_access_token(token)["store_id"])
    mgr = User(store_id=store_id, username="mgr-cash", password_hash="h", role=UserRole.MANAGER)
    session.add(mgr)
    await session.flush()
    return encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store_id)


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

    # 手動現金調整（可正可負；限 MANAGER、事由必填留痕）。
    manager_token = await _seed_manager(db_session, token)
    adj = await client.post(
        f"/api/v1/cash-sessions/{session_id}/movements",
        json={"type": "MANUAL_ADJUST", "amount": "-50", "note": "找錯錢回沖"},
        headers=_auth(manager_token),
    )
    assert adj.status_code == 201
    assert adj.json()["amount"] == "-50"
    assert adj.json()["note"] == "找錯錢回沖"  # 事由須持久化（Codex P2）

    movements = await client.get(
        f"/api/v1/cash-sessions/{session_id}/movements", headers=_auth(token)
    )
    assert movements.status_code == 200
    assert [
        (movement["type"], movement["amount"], movement["note"]) for movement in movements.json()
    ] == [("MANUAL_ADJUST", "-50", "找錯錢回沖")]

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


async def test_system_movement_types_rejected_via_api(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """端點僅限 MANUAL_ADJUST：SALE_IN 等系統類型由內部流程產生，API 灌入一律 422
    （否則任何登入者可憑空捏造營收現金流）。"""
    token = await _seed_token(db_session)
    opened = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    session_id = opened.json()["id"]
    manager_token = await _seed_manager(db_session, token)
    resp = await client.post(
        f"/api/v1/cash-sessions/{session_id}/movements",
        json={"type": "SALE_IN", "amount": "10", "note": "x"},
        headers=_auth(manager_token),
    )
    assert resp.status_code == 422


async def test_manual_adjust_requires_manager(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """CLERK 直接打端點 → 403（前端隱藏不等於安全，Codex P1）。"""
    token = await _seed_token(db_session)
    opened = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    session_id = opened.json()["id"]
    resp = await client.post(
        f"/api/v1/cash-sessions/{session_id}/movements",
        json={"type": "MANUAL_ADJUST", "amount": "-50", "note": "x"},
        headers=_auth(token),
    )
    assert resp.status_code == 403


async def test_manual_adjust_requires_note(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """事由必填（留痕，CLAUDE.md §5）：缺 note → 422。"""
    token = await _seed_token(db_session)
    opened = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    session_id = opened.json()["id"]
    manager_token = await _seed_manager(db_session, token)
    resp = await client.post(
        f"/api/v1/cash-sessions/{session_id}/movements",
        json={"type": "MANUAL_ADJUST", "amount": "-50"},
        headers=_auth(manager_token),
    )
    assert resp.status_code == 422


async def test_manual_adjust_blank_note_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """純空白事由 → 422（等同無留痕；後端為權威，Codex P2）。"""
    token = await _seed_token(db_session)
    opened = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    session_id = opened.json()["id"]
    manager_token = await _seed_manager(db_session, token)
    resp = await client.post(
        f"/api/v1/cash-sessions/{session_id}/movements",
        json={"type": "MANUAL_ADJUST", "amount": "10", "note": "   "},
        headers=_auth(manager_token),
    )
    assert resp.status_code == 422


async def test_manual_adjust_note_written_to_audit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from sqlalchemy import select

    from app.core.audit import AuditLog

    token = await _seed_token(db_session)
    opened = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    session_id = opened.json()["id"]
    manager_token = await _seed_manager(db_session, token)
    resp = await client.post(
        f"/api/v1/cash-sessions/{session_id}/movements",
        json={"type": "MANUAL_ADJUST", "amount": "200", "note": "補零錢箱"},
        headers=_auth(manager_token),
    )
    assert resp.status_code == 201
    logs = (await db_session.scalars(select(AuditLog))).all()
    adjust_logs = [log for log in logs if "ADJUST" in log.action.upper()]
    assert len(adjust_logs) >= 1
    after = adjust_logs[-1].after or {}
    assert after.get("note") == "補零錢箱"


async def test_movement_on_wrong_session_returns_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = await _seed_token(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    manager_token = await _seed_manager(db_session, token)
    resp = await client.post(
        "/api/v1/cash-sessions/999999/movements",
        json={"type": "MANUAL_ADJUST", "amount": "10", "note": "x"},
        headers=_auth(manager_token),
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


async def test_scientific_notation_opening_float_returns_422(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """開帳金額必須使用一般整數格式，字串或 JSON 數字的科學記號都不可繞過。"""
    token = await _seed_token(db_session)

    string_notation = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1e3"}, headers=_auth(token)
    )
    numeric_notation = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": 1e3}, headers=_auth(token)
    )

    assert string_notation.status_code == 422
    assert numeric_notation.status_code == 422


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
