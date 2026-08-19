"""叫號系統 API 整合測試（docs/38）：權限、連結安全、清單語意、完成冪等。"""

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole

pytestmark = pytest.mark.asyncio


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
    """建店＋店員＋經理，回 (clerk_token, kiosk_token, store_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="ct-clk", password_hash="h", role=UserRole.CLERK)
    kiosk = User(store_id=store.id, username="ct-kio", password_hash="h", role=UserRole.KIOSK)
    session.add_all([clerk, kiosk])
    await session.flush()
    return (
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=kiosk.id, role="KIOSK", store_id=store.id),
        store.id,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_clerk_can_create_and_complete(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """裁示：店員也可以。排隊是日常作業，卡權限反而礙事。"""
    clerk, _, _ = await _seed(db_session)
    created = await client.post(
        "/api/v1/call-tickets",
        json={"name": "王先生", "link": "https://example.com/f/1", "note": "帳篷"},
        headers=_auth(clerk),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["ticket_no"] == 1
    assert body["status"] == "WAITING"

    done = await client.post(
        f"/api/v1/call-tickets/{body['id']}/complete", headers=_auth(clerk)
    )
    assert done.status_code == 200
    assert done.json()["status"] == "DONE"


async def test_kiosk_is_rejected(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """KIOSK 是手持簽署裝置專用身分，碰不到任何店務資料。"""
    _, kiosk, _ = await _seed(db_session)
    resp = await client.post(
        "/api/v1/call-tickets", json={"name": "不該進來"}, headers=_auth(kiosk)
    )
    assert resp.status_code in (401, 403), resp.text


async def test_unauthenticated_is_rejected(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/call-tickets")).status_code == 401


@pytest.mark.parametrize(
    "bad_link",
    ["javascript:alert(1)", "data:text/html,<script>x</script>", "file:///etc/passwd"],
)
async def test_dangerous_link_schemes_are_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, bad_link: str
) -> None:
    """**這個連結會被店員點開**——擋在邊界，不要等到前端才防。"""
    clerk, _, _ = await _seed(db_session)
    resp = await client.post(
        "/api/v1/call-tickets",
        json={"name": "壞連結", "link": bad_link},
        headers=_auth(clerk),
    )
    assert resp.status_code == 422, resp.text


async def test_blank_link_and_note_become_null(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """空字串正規化為 NULL——不要在畫面上留一個可點的空連結。"""
    clerk, _, _ = await _seed(db_session)
    resp = await client.post(
        "/api/v1/call-tickets",
        json={"name": "  陳小姐  ", "link": "   ", "note": ""},
        headers=_auth(clerk),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert (body["link"], body["note"]) == (None, None)
    assert body["name"] == "陳小姐"  # 前後空白去掉


async def test_blank_name_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, _, _ = await _seed(db_session)
    resp = await client.post(
        "/api/v1/call-tickets", json={"name": "   "}, headers=_auth(clerk)
    )
    assert resp.status_code == 422


async def test_list_defaults_to_waiting_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, _, _ = await _seed(db_session)
    keep = (
        await client.post("/api/v1/call-tickets", json={"name": "留著"}, headers=_auth(clerk))
    ).json()
    gone = (
        await client.post("/api/v1/call-tickets", json={"name": "完成"}, headers=_auth(clerk))
    ).json()
    await client.post(f"/api/v1/call-tickets/{gone['id']}/complete", headers=_auth(clerk))

    waiting = (await client.get("/api/v1/call-tickets", headers=_auth(clerk))).json()
    assert [t["id"] for t in waiting] == [keep["id"]]

    everything = (
        await client.get("/api/v1/call-tickets?include_done=true", headers=_auth(clerk))
    ).json()
    assert {t["id"] for t in everything} == {keep["id"], gone["id"]}


async def test_complete_twice_is_idempotent_over_http(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """手滑連按兩下不該看到錯誤。"""
    clerk, _, _ = await _seed(db_session)
    created = (
        await client.post("/api/v1/call-tickets", json={"name": "王先生"}, headers=_auth(clerk))
    ).json()
    first = await client.post(
        f"/api/v1/call-tickets/{created['id']}/complete", headers=_auth(clerk)
    )
    second = await client.post(
        f"/api/v1/call-tickets/{created['id']}/complete", headers=_auth(clerk)
    )
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["completed_at"] == second.json()["completed_at"]


async def test_complete_unknown_ticket_is_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    clerk, _, _ = await _seed(db_session)
    resp = await client.post("/api/v1/call-tickets/999999/complete", headers=_auth(clerk))
    assert resp.status_code == 404
