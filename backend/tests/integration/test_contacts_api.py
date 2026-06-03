"""contacts API 整合測試（對齊 CLAUDE.md §7 PII 與 T4 必測 1-4）。

client fixture 以 dependency_overrides 把 app 的 get_session 換成測試的回滾隔離 session，
使 API 寫入與測試共用同一交易、結束自動回滾。
"""

from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.crypto import get_pii_cipher
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole

NATIONAL_ID = "A123456789"


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override_get_session() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _setup_store_and_tokens(session: AsyncSession) -> tuple[int, str, str]:
    """建立 store 與 MANAGER/CLERK，回傳 (store_id, manager_token, clerk_token)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    manager = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add_all([manager, clerk])
    await session.flush()
    m_token = encode_access_token(user_id=manager.id, role="MANAGER", store_id=store.id)
    c_token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return store.id, m_token, c_token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── 必測 1：DB 為密文、一般回應遮罩 ──
async def test_create_stores_ciphertext_and_response_is_masked(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)

    resp = await client.post(
        "/api/v1/contacts",
        json={"name": "王小明", "national_id": NATIONAL_ID, "roles": ["MEMBER"]},
        headers=_auth(m_token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_national_id"] is True
    assert body["national_id_masked"] == "***"
    assert NATIONAL_ID not in resp.text  # 回應不含明文

    # DB 內為密文，且可解回原值。
    contact = await db_session.scalar(select(Contact).where(Contact.id == body["id"]))
    assert contact is not None
    assert contact.national_id_enc is not None
    assert contact.national_id_enc != NATIONAL_ID
    assert get_pii_cipher().decrypt(contact.national_id_enc) == NATIONAL_ID


async def test_get_contact_masks_national_id(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    created = await client.post(
        "/api/v1/contacts",
        json={"name": "李四", "national_id": NATIONAL_ID, "roles": ["SELLER"]},
        headers=_auth(m_token),
    )
    cid = created.json()["id"]

    resp = await client.get(f"/api/v1/contacts/{cid}", headers=_auth(m_token))
    assert resp.status_code == 200
    assert resp.json()["national_id_masked"] == "***"
    assert NATIONAL_ID not in resp.text


# ── 必測 2：blind index 精確去重；明文/部分搜尋查不到 ──
async def test_lookup_and_create_dedup_by_blind_index(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    first = await client.post(
        "/api/v1/contacts",
        json={"name": "王五", "national_id": NATIONAL_ID, "roles": ["SELLER"]},
        headers=_auth(m_token),
    )
    cid = first.json()["id"]

    # lookup 命中既有（national_id 放 body）。
    found = await client.post(
        "/api/v1/contacts/lookup",
        json={"national_id": NATIONAL_ID},
        headers=_auth(m_token),
    )
    assert found.status_code == 200
    assert found.json()["id"] == cid

    # 以相同 national_id 再建檔 → 命中既有、不新增。
    again = await client.post(
        "/api/v1/contacts",
        json={"name": "另一個名字", "national_id": NATIONAL_ID, "roles": ["SELLER"]},
        headers=_auth(m_token),
    )
    assert again.json()["id"] == cid


async def test_plaintext_and_partial_search_cannot_find_national_id(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    await client.post(
        "/api/v1/contacts",
        json={"name": "陳六", "national_id": NATIONAL_ID, "roles": ["MEMBER"]},
        headers=_auth(m_token),
    )

    # 以 national_id 明文/部分當 q 搜尋 → 查不到。
    for q in (NATIONAL_ID, NATIONAL_ID[:5]):
        resp = await client.get("/api/v1/contacts", params={"q": q}, headers=_auth(m_token))
        assert resp.status_code == 200
        assert resp.json() == []

    # 以姓名搜尋 → 找得到。
    by_name = await client.get("/api/v1/contacts", params={"q": "陳六"}, headers=_auth(m_token))
    assert len(by_name.json()) == 1


# ── 必測 3：RBAC + 解密寫稽核（稽核不含明文）──
async def test_reveal_national_id_rbac_and_audit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, m_token, c_token = await _setup_store_and_tokens(db_session)
    created = await client.post(
        "/api/v1/contacts",
        json={"name": "趙七", "national_id": NATIONAL_ID, "roles": ["SELLER"]},
        headers=_auth(m_token),
    )
    cid = created.json()["id"]
    url = f"/api/v1/contacts/{cid}/national-id"

    # 無 token → 401；壞 token → 401。
    assert (await client.get(url)).status_code == 401
    assert (await client.get(url, headers=_auth("garbage.token"))).status_code == 401

    # CLERK → 403。
    assert (await client.get(url, headers=_auth(c_token))).status_code == 403

    # MANAGER → 200 + 回明文。
    ok = await client.get(url, headers=_auth(m_token))
    assert ok.status_code == 200
    assert ok.json()["national_id"] == NATIONAL_ID

    # 寫了一筆稽核，且不含 national_id 明文。
    rows = list(
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "VIEW_NATIONAL_ID", AuditLog.store_id == store_id
            )
        )
    )
    assert len(rows) == 1
    entry = rows[0]
    assert entry.is_sensitive is True
    assert entry.entity_id == str(cid)
    assert NATIONAL_ID not in str(entry.before)
    assert NATIONAL_ID not in str(entry.after)


async def test_expired_token_rejected(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    from datetime import timedelta

    store_id, _, _ = await _setup_store_and_tokens(db_session)
    expired = encode_access_token(
        user_id=1, role="MANAGER", store_id=store_id, expires_delta=timedelta(minutes=-1)
    )
    resp = await client.get("/api/v1/contacts", headers=_auth(expired))
    assert resp.status_code == 401


# ── 必測 4：多重角色；收購/寄售必填 national_id ──
async def test_contact_can_hold_multiple_roles(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    resp = await client.post(
        "/api/v1/contacts",
        json={
            "name": "多角色",
            "national_id": NATIONAL_ID,
            "roles": ["MEMBER", "SELLER", "CONSIGNOR"],
        },
        headers=_auth(m_token),
    )
    assert resp.status_code == 201
    assert set(resp.json()["roles"]) == {"MEMBER", "SELLER", "CONSIGNOR"}


@pytest.mark.parametrize("role", ["SELLER", "CONSIGNOR"])
async def test_acquisition_role_requires_national_id(
    client: httpx.AsyncClient, db_session: AsyncSession, role: str
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    resp = await client.post(
        "/api/v1/contacts",
        json={"name": "缺證號", "roles": [role]},
        headers=_auth(m_token),
    )
    assert resp.status_code == 422


async def test_member_without_national_id_is_allowed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    resp = await client.post(
        "/api/v1/contacts",
        json={"name": "純會員", "roles": ["MEMBER"]},
        headers=_auth(m_token),
    )
    assert resp.status_code == 201
    assert resp.json()["has_national_id"] is False
    assert resp.json()["national_id_masked"] is None


# ── 額外分支覆蓋 ──
async def test_get_contact_404_when_missing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    resp = await client.get("/api/v1/contacts/999999", headers=_auth(m_token))
    assert resp.status_code == 404


async def test_reveal_404_when_contact_missing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    resp = await client.get("/api/v1/contacts/999999/national-id", headers=_auth(m_token))
    assert resp.status_code == 404


async def test_reveal_404_when_contact_has_no_national_id(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    created = await client.post(
        "/api/v1/contacts",
        json={"name": "純會員", "roles": ["MEMBER"]},
        headers=_auth(m_token),
    )
    cid = created.json()["id"]
    resp = await client.get(f"/api/v1/contacts/{cid}/national-id", headers=_auth(m_token))
    assert resp.status_code == 404


async def test_lookup_miss_returns_null(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    resp = await client.post(
        "/api/v1/contacts/lookup",
        json={"national_id": "Z999999999"},
        headers=_auth(m_token),
    )
    assert resp.status_code == 200
    assert resp.json() is None


async def test_list_filter_by_role(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    await client.post(
        "/api/v1/contacts",
        json={"name": "賣家", "national_id": NATIONAL_ID, "roles": ["SELLER"]},
        headers=_auth(m_token),
    )
    await client.post(
        "/api/v1/contacts",
        json={"name": "會員", "roles": ["MEMBER"]},
        headers=_auth(m_token),
    )
    sellers = await client.get(
        "/api/v1/contacts", params={"role": "SELLER"}, headers=_auth(m_token)
    )
    assert sellers.status_code == 200
    assert [c["name"] for c in sellers.json()] == ["賣家"]
