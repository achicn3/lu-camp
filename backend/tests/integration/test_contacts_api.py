"""contacts API 整合測試（對齊 CLAUDE.md §7 PII 與 T4 必測 1-4）。

client fixture 以 dependency_overrides 把 app 的 get_session 換成測試的回滾隔離 session，
使 API 寫入與測試共用同一交易、結束自動回滾。
"""

import itertools
from collections.abc import AsyncGenerator
from decimal import Decimal

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
from app.modules.storecredit.models import StoreCreditAccount
from app.modules.user.models import User
from app.shared.enums import UserRole

NATIONAL_ID = "A123456789"

_phone_seq = itertools.count(1)


def _uphone() -> str:
    """測試用唯一手機（手機同店唯一、必填）。"""
    return f"09{next(_phone_seq):08d}"


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
        json={
            "phone": _uphone(),
            "name": "王小明",
            "national_id": NATIONAL_ID,
            "roles": ["MEMBER"],
        },
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


async def test_create_rejects_invalid_national_id_without_leaking_value(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """格式/檢核碼錯誤的 national_id → 422，且回應不得含輸入值（PII，CLAUDE.md §5）。"""
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    bad = "A123456788"  # 末碼錯一位
    resp = await client.post(
        "/api/v1/contacts",
        json={"phone": _uphone(), "name": "格式錯", "national_id": bad, "roles": ["MEMBER"]},
        headers=_auth(m_token),
    )
    assert resp.status_code == 422, resp.text
    assert bad not in resp.text  # 不洩漏輸入值
    # 不落地
    contact = await db_session.scalar(select(Contact).where(Contact.name == "格式錯"))
    assert contact is None


async def test_update_rejects_invalid_national_id(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """PATCH 改 national_id 為非法值 → 422，且不洩漏輸入值。"""
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    created = await client.post(
        "/api/v1/contacts",
        json={
            "phone": _uphone(),
            "name": "改身分證",
            "national_id": NATIONAL_ID,
            "roles": ["MEMBER"],
        },
        headers=_auth(m_token),
    )
    cid = created.json()["id"]
    bad = "Z999999999"
    resp = await client.patch(
        f"/api/v1/contacts/{cid}",
        json={"national_id": bad},
        headers=_auth(m_token),
    )
    assert resp.status_code == 422, resp.text
    assert bad not in resp.text


async def test_create_requires_phone(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """手機必填：建檔未帶手機 → 422。"""
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    resp = await client.post(
        "/api/v1/contacts",
        json={"name": "無手機", "roles": ["MEMBER"]},
        headers=_auth(m_token),
    )
    assert resp.status_code == 422, resp.text


async def test_create_duplicate_phone_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """手機同店唯一：撞號 → 409（請改以手機查找既有會員）。"""
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    phone = "0911222333"
    first = await client.post(
        "/api/v1/contacts",
        json={"name": "甲", "phone": phone, "roles": ["MEMBER"]},
        headers=_auth(m_token),
    )
    assert first.status_code == 201
    dup = await client.post(
        "/api/v1/contacts",
        json={"name": "乙", "phone": phone, "roles": ["MEMBER"]},
        headers=_auth(m_token),
    )
    assert dup.status_code == 409, dup.text


async def test_update_duplicate_phone_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """PATCH 改手機撞到同店他人 → 409。"""
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    a = await _create_contact(client, m_token, name="甲", phone="0911000001", roles=["MEMBER"])
    await _create_contact(client, m_token, name="乙", phone="0911000002", roles=["MEMBER"])
    resp = await client.patch(
        f"/api/v1/contacts/{a}", json={"phone": "0911000002"}, headers=_auth(m_token)
    )
    assert resp.status_code == 409, resp.text


async def test_get_contact_masks_national_id(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    created = await client.post(
        "/api/v1/contacts",
        json={"phone": _uphone(), "name": "李四", "national_id": NATIONAL_ID, "roles": ["SELLER"]},
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
        json={"phone": _uphone(), "name": "王五", "national_id": NATIONAL_ID, "roles": ["SELLER"]},
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
        json={
            "phone": _uphone(),
            "name": "另一個名字",
            "national_id": NATIONAL_ID,
            "roles": ["SELLER"],
        },
        headers=_auth(m_token),
    )
    assert again.json()["id"] == cid


async def test_plaintext_and_partial_search_cannot_find_national_id(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    await client.post(
        "/api/v1/contacts",
        json={"phone": _uphone(), "name": "陳六", "national_id": NATIONAL_ID, "roles": ["MEMBER"]},
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
        json={"phone": _uphone(), "name": "趙七", "national_id": NATIONAL_ID, "roles": ["SELLER"]},
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
            "phone": _uphone(),
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
        json={"phone": _uphone(), "name": "缺證號", "roles": [role]},
        headers=_auth(m_token),
    )
    assert resp.status_code == 422


async def test_member_without_national_id_is_allowed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    resp = await client.post(
        "/api/v1/contacts",
        json={"phone": _uphone(), "name": "純會員", "roles": ["MEMBER"]},
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
        json={"phone": _uphone(), "name": "純會員", "roles": ["MEMBER"]},
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
        json={"phone": _uphone(), "name": "賣家", "national_id": NATIONAL_ID, "roles": ["SELLER"]},
        headers=_auth(m_token),
    )
    await client.post(
        "/api/v1/contacts",
        json={"phone": _uphone(), "name": "會員", "roles": ["MEMBER"]},
        headers=_auth(m_token),
    )
    sellers = await client.get(
        "/api/v1/contacts", params={"role": "SELLER"}, headers=_auth(m_token)
    )
    assert sellers.status_code == 200
    assert [c["name"] for c in sellers.json()] == ["賣家"]


async def test_list_pagination(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    for i in range(3):
        await client.post(
            "/api/v1/contacts",
            json={"phone": _uphone(), "name": f"會員{i}", "roles": ["MEMBER"]},
            headers=_auth(m_token),
        )
    page1 = await client.get("/api/v1/contacts", params={"limit": 2}, headers=_auth(m_token))
    assert len(page1.json()) == 2
    page2 = await client.get(
        "/api/v1/contacts", params={"limit": 2, "offset": 2}, headers=_auth(m_token)
    )
    assert len(page2.json()) == 1
    over = await client.get("/api/v1/contacts", params={"limit": 201}, headers=_auth(m_token))
    assert over.status_code == 422


# ── T21-a：PATCH /contacts/{id}（會員編輯；docs/17 §5.2、裁示 #3）──


async def _create_contact(client: httpx.AsyncClient, token: str, **fields: object) -> int:
    fields.setdefault("phone", _uphone())  # 手機必填、同店唯一
    resp = await client.post("/api/v1/contacts", json=fields, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def test_update_contact_clerk_edits_basic_fields_and_audits(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, m_token, c_token = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(
        client, m_token, name="原名", phone="0911000000", roles=["MEMBER"], source_note="x"
    )

    resp = await client.patch(
        f"/api/v1/contacts/{cid}",
        json={"name": "新名", "phone": "0922222222", "source_note": "VIP"},
        headers=_auth(c_token),  # CLERK 可編輯一般欄位 + 電話
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "新名"
    assert body["phone"] == "0922222222"
    assert body["source_note"] == "VIP"

    # 電話/一般欄位變更寫 UPDATE_CONTACT 稽核（裁示 #3：電話一律留痕）。
    rows = list(
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "UPDATE_CONTACT", AuditLog.store_id == store_id
            )
        )
    )
    assert len(rows) == 1
    assert rows[0].entity_id == str(cid)


async def test_update_contact_partial_leaves_unset_fields_intact(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, c_token = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(
        client, m_token, name="原名", phone="0911", roles=["MEMBER"], source_note="保留"
    )

    resp = await client.patch(
        f"/api/v1/contacts/{cid}", json={"phone": "0999"}, headers=_auth(c_token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["phone"] == "0999"
    assert body["name"] == "原名"  # 未提供 → 不動
    assert body["source_note"] == "保留"
    assert body["roles"] == ["MEMBER"]


async def test_update_contact_clerk_cannot_remove_role(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """CLERK 角色只增不減：移除既有角色仍需 MANAGER（補登放寬只允許新增，2026-06-24）。"""
    _, m_token, c_token = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(
        client, m_token, name="會員", national_id=NATIONAL_ID, roles=["MEMBER", "SELLER"]
    )
    resp = await client.patch(
        f"/api/v1/contacts/{cid}", json={"roles": ["MEMBER"]}, headers=_auth(c_token)
    )
    assert resp.status_code == 403


async def test_update_contact_clerk_cannot_change_existing_national_id(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """CLERK 只能補登（原本無）；覆蓋或清空既有 national_id 仍需 MANAGER。"""
    _, m_token, c_token = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(
        client, m_token, name="會員", national_id=NATIONAL_ID, roles=["MEMBER"]
    )
    # 覆蓋既有 → 403
    overwrite = await client.patch(
        f"/api/v1/contacts/{cid}", json={"national_id": "A223456781"}, headers=_auth(c_token)
    )
    assert overwrite.status_code == 403
    # 清空既有 → 403
    clear = await client.patch(
        f"/api/v1/contacts/{cid}", json={"national_id": None}, headers=_auth(c_token)
    )
    assert clear.status_code == 403


async def test_update_contact_clerk_can_backfill_national_id_and_add_seller(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """補登（裁示 #3 放寬，2026-06-24）：CLERK 可為原本無證號的會員設定 national_id
    並同時新增 SELLER 角色（收購櫃檯一條龍）。"""
    _, m_token, c_token = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(client, m_token, name="買斷會員", roles=["MEMBER"])
    resp = await client.patch(
        f"/api/v1/contacts/{cid}",
        json={"national_id": NATIONAL_ID, "roles": ["MEMBER", "SELLER"]},
        headers=_auth(c_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_national_id"] is True
    assert set(body["roles"]) == {"MEMBER", "SELLER"}


async def test_update_contact_manager_changes_roles(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    # 已有 national_id → 可加 SELLER 角色。
    cid = await _create_contact(
        client, m_token, name="會員", national_id=NATIONAL_ID, roles=["MEMBER"]
    )
    resp = await client.patch(
        f"/api/v1/contacts/{cid}", json={"roles": ["MEMBER", "SELLER"]}, headers=_auth(m_token)
    )
    assert resp.status_code == 200
    assert set(resp.json()["roles"]) == {"MEMBER", "SELLER"}


async def test_update_contact_add_acquisition_role_without_national_id_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(client, m_token, name="會員", roles=["MEMBER"])
    resp = await client.patch(
        f"/api/v1/contacts/{cid}", json={"roles": ["MEMBER", "SELLER"]}, headers=_auth(m_token)
    )
    assert resp.status_code == 422  # SELLER/CONSIGNOR 必須有 national_id


async def test_update_contact_manager_sets_national_id_encrypts_and_audits(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, m_token, _ = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(client, m_token, name="會員", roles=["MEMBER"])

    resp = await client.patch(
        f"/api/v1/contacts/{cid}", json={"national_id": NATIONAL_ID}, headers=_auth(m_token)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_national_id"] is True
    assert body["national_id_masked"] == "***"
    assert NATIONAL_ID not in resp.text

    contact = await db_session.scalar(select(Contact).where(Contact.id == cid))
    assert contact is not None
    assert contact.national_id_enc is not None
    assert get_pii_cipher().decrypt(contact.national_id_enc) == NATIONAL_ID
    assert contact.national_id_blind_index is not None

    rows = list(
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "UPDATE_CONTACT_PII", AuditLog.store_id == store_id
            )
        )
    )
    assert len(rows) == 1
    assert rows[0].is_sensitive is True
    assert NATIONAL_ID not in str(rows[0].before)
    assert NATIONAL_ID not in str(rows[0].after)


async def test_update_contact_duplicate_national_id_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    await _create_contact(client, m_token, name="甲", national_id=NATIONAL_ID, roles=["SELLER"])
    other = await _create_contact(client, m_token, name="乙", roles=["MEMBER"])

    resp = await client.patch(
        f"/api/v1/contacts/{other}", json={"national_id": NATIONAL_ID}, headers=_auth(m_token)
    )
    assert resp.status_code == 409


async def test_update_contact_blank_national_id_with_acquisition_role_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # 空字串不可偽裝為有效 national_id 來滿足 SELLER/CONSIGNOR（Codex 對抗式審查 high）。
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(client, m_token, name="會員", roles=["MEMBER"])
    resp = await client.patch(
        f"/api/v1/contacts/{cid}",
        json={"national_id": "", "roles": ["MEMBER", "SELLER"]},
        headers=_auth(m_token),
    )
    assert resp.status_code == 422


async def test_update_contact_whitespace_national_id_treated_as_clear(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(
        client, m_token, name="會員", national_id=NATIONAL_ID, roles=["MEMBER"]
    )
    resp = await client.patch(
        f"/api/v1/contacts/{cid}", json={"national_id": "   "}, headers=_auth(m_token)
    )
    assert resp.status_code == 200
    assert resp.json()["has_national_id"] is False
    contact = await db_session.scalar(select(Contact).where(Contact.id == cid))
    assert contact is not None
    assert contact.national_id_enc is None
    assert contact.national_id_blind_index is None


async def test_update_contact_clear_national_id(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(
        client, m_token, name="會員", national_id=NATIONAL_ID, roles=["MEMBER"]
    )
    resp = await client.patch(
        f"/api/v1/contacts/{cid}", json={"national_id": None}, headers=_auth(m_token)
    )
    assert resp.status_code == 200
    assert resp.json()["has_national_id"] is False
    contact = await db_session.scalar(select(Contact).where(Contact.id == cid))
    assert contact is not None
    assert contact.national_id_enc is None
    assert contact.national_id_blind_index is None


async def test_update_contact_carrier_fields(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, c_token = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(client, m_token, name="會員", roles=["MEMBER"])
    resp = await client.patch(
        f"/api/v1/contacts/{cid}",
        json={"default_carrier_type": "3J0002", "default_carrier_id": "/ABC1234"},
        headers=_auth(c_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["default_carrier_type"] == "3J0002"
    assert body["default_carrier_id"] == "/ABC1234"


async def test_update_contact_name_null_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, c_token = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(client, m_token, name="會員", roles=["MEMBER"])
    resp = await client.patch(
        f"/api/v1/contacts/{cid}", json={"name": None}, headers=_auth(c_token)
    )
    assert resp.status_code == 422


async def test_update_contact_cannot_remove_member_with_store_credit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # 仍持有購物金的會員不可被移除 MEMBER（否則非會員仍掛負債；Codex high）。
    store_id, m_token, _ = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(client, m_token, name="會員", roles=["MEMBER"])
    db_session.add(StoreCreditAccount(store_id=store_id, contact_id=cid, balance=Decimal(100)))
    await db_session.flush()

    resp = await client.patch(f"/api/v1/contacts/{cid}", json={"roles": []}, headers=_auth(m_token))
    # 409 即守衛生效；角色變更隨 router 的整筆 rollback 一併丟棄（不在共用 session 後驗，
    # 因錯誤路徑 rollback 會回退共用測試 session 的狀態）。
    assert resp.status_code == 409


async def test_update_contact_can_remove_member_without_store_credit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(client, m_token, name="會員", roles=["MEMBER"])
    resp = await client.patch(f"/api/v1/contacts/{cid}", json={"roles": []}, headers=_auth(m_token))
    assert resp.status_code == 200
    assert resp.json()["roles"] == []


async def test_update_contact_keep_member_add_seller_with_store_credit_ok(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # 保留 MEMBER、加 SELLER（有 national_id）→ 未移除 MEMBER，不受守衛限制。
    store_id, m_token, _ = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(
        client, m_token, name="會員", national_id=NATIONAL_ID, roles=["MEMBER"]
    )
    db_session.add(StoreCreditAccount(store_id=store_id, contact_id=cid, balance=Decimal(50)))
    await db_session.flush()

    resp = await client.patch(
        f"/api/v1/contacts/{cid}", json={"roles": ["MEMBER", "SELLER"]}, headers=_auth(m_token)
    )
    assert resp.status_code == 200
    assert set(resp.json()["roles"]) == {"MEMBER", "SELLER"}


async def test_update_contact_404_when_missing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, m_token, _ = await _setup_store_and_tokens(db_session)
    resp = await client.patch("/api/v1/contacts/999999", json={"name": "x"}, headers=_auth(m_token))
    assert resp.status_code == 404


async def test_update_contact_cross_store_is_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # store A 建會員；store B 的使用者不可改到 A 的會員（多分店隔離）。
    _, m_token_a, _ = await _setup_store_and_tokens(db_session)
    cid = await _create_contact(client, m_token_a, name="A店會員", roles=["MEMBER"])
    store_b = Store(name="B店")
    db_session.add(store_b)
    await db_session.flush()
    mgr_b = User(store_id=store_b.id, username="mgrb", password_hash="h", role=UserRole.MANAGER)
    db_session.add(mgr_b)
    await db_session.flush()
    token_b = encode_access_token(user_id=mgr_b.id, role="MANAGER", store_id=store_b.id)

    resp = await client.patch(
        f"/api/v1/contacts/{cid}", json={"name": "竄改"}, headers=_auth(token_b)
    )
    assert resp.status_code == 404
