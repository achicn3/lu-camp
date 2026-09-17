"""切結書內文可由店家自行修改（2026-09-17 需求）。

**改內容＝發新版本，不是改字**：`agreement_versions` 是不可變版本表，已簽的簽名綁著
簽署當下那一列（docs/23 §5）。因此「儲存」要落成新的一列、版本號 +1，舊列一字不動——
日後爭議時才拿得出「客人當初簽的是哪一份」。

另一條不變量是多分店（CLAUDE.md §4）：版本號以**店**為單位遞增，A 店改內容不得動到
B 店的切結書，也不得害 B 店的版本號跳號。
"""

from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.signing.agreements import AGREEMENT_TEXTS, CURRENT_AGREEMENT_VERSION
from app.modules.signing.models import AgreementVersion
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


async def _seed(session: AsyncSession, *, name: str = "門市") -> tuple[str, str, int]:
    """建店＋經理＋店員，回 (mgr_token, clerk_token, store_id)。"""
    store = Store(name=name)
    session.add(store)
    await session.flush()
    mgr = User(
        store_id=store.id, username=f"mgr-{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    clerk = User(
        store_id=store.id, username=f"clk-{store.id}", password_hash="h", role=UserRole.CLERK
    )
    session.add_all([mgr, clerk])
    await session.flush()
    return (
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        store.id,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_current_returns_builtin_text_before_any_edit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """還沒改過就回內建全文——編輯視窗要能以「現在這份」開場，不是空白。"""
    mgr, _, _ = await _seed(db_session)
    resp = await client.get("/api/v1/agreements/current", headers=_auth(mgr))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    title, text = AGREEMENT_TEXTS[CURRENT_AGREEMENT_VERSION]
    assert body["version"] == CURRENT_AGREEMENT_VERSION
    assert body["title"] == title
    assert body["body"] == text


async def test_save_creates_new_version_and_keeps_old_row_intact(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _, store_id = await _seed(db_session)
    before = await client.get("/api/v1/agreements/current", headers=_auth(mgr))
    old_version = before.json()["version"]
    old_body = before.json()["body"]

    saved = await client.post(
        "/api/v1/agreements",
        json={
            "title": "二手商品讓售切結書",
            "body": "一、新版內文\n\n二、第二條",
            "expected_version": old_version,
        },
        headers=_auth(mgr),
    )
    assert saved.status_code == 201, saved.text
    assert saved.json()["version"] == old_version + 1
    assert saved.json()["body"] == "一、新版內文\n\n二、第二條"

    # 舊版列一字不動（已簽的簽名綁著它）
    old_row = await db_session.scalar(
        select(AgreementVersion).where(
            AgreementVersion.store_id == store_id, AgreementVersion.version == old_version
        )
    )
    assert old_row is not None
    assert old_row.body == old_body

    # 之後讀 current 回新版
    after = await client.get("/api/v1/agreements/current", headers=_auth(mgr))
    assert after.json()["version"] == old_version + 1


async def test_save_is_audited(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """改切結書是敏感操作（§5）：誰、何時、改成第幾版都要留紀錄。"""
    mgr, _, _ = await _seed(db_session)
    current = (await client.get("/api/v1/agreements/current", headers=_auth(mgr))).json()
    await client.post(
        "/api/v1/agreements",
        json={"title": "新標題", "body": "新內文", "expected_version": current["version"]},
        headers=_auth(mgr),
    )
    log = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "UPDATE_AGREEMENT_TEXT")
    )
    assert log is not None
    assert log.before["version"] is not None
    assert log.after["title"] == "新標題"


async def test_unchanged_text_does_not_bump_version(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """按了儲存但一個字都沒改＝不發新版：否則每次打開關掉都多一版，版本號失去意義。"""
    mgr, _, _ = await _seed(db_session)
    current = (await client.get("/api/v1/agreements/current", headers=_auth(mgr))).json()
    again = await client.post(
        "/api/v1/agreements",
        json={
            "title": current["title"],
            "body": current["body"],
            "expected_version": current["version"],
        },
        headers=_auth(mgr),
    )
    assert again.status_code == 200  # 200＝沿用現版，非 201
    assert again.json()["version"] == current["version"]


async def test_clerk_cannot_read_or_edit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _, clerk, _ = await _seed(db_session)
    assert (await client.get("/api/v1/agreements/current", headers=_auth(clerk))).status_code == 403
    resp = await client.post(
        "/api/v1/agreements",
        json={"title": "x", "body": "y", "expected_version": 1},
        headers=_auth(clerk),
    )
    assert resp.status_code == 403


async def test_blank_and_oversized_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """空白內容會讓客人簽到一張白紙；超長則在手持裝置上讀不完也印不出。"""
    mgr, _, _ = await _seed(db_session)
    version = (await client.get("/api/v1/agreements/current", headers=_auth(mgr))).json()["version"]
    for bad in (
        {"title": "   ", "body": "有內容", "expected_version": version},
        {"title": "標題", "body": "   \n  ", "expected_version": version},
        {"title": "標題", "body": "字" * 20001, "expected_version": version},
        {"title": "標" * 101, "body": "內文", "expected_version": version},
    ):
        resp = await client.post("/api/v1/agreements", json=bad, headers=_auth(mgr))
        assert resp.status_code == 422, (bad, resp.text)


async def test_crlf_normalized_so_layout_stays_predictable(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """貼上的內容常帶 \\r\\n；不正規化的話手持端會多出空行、跑版。"""
    mgr, _, _ = await _seed(db_session)
    version = (await client.get("/api/v1/agreements/current", headers=_auth(mgr))).json()["version"]
    saved = await client.post(
        "/api/v1/agreements",
        json={
            "title": "標題",
            "body": "第一段\r\n\r\n第二段\r\n",
            "expected_version": version,
        },
        headers=_auth(mgr),
    )
    assert saved.status_code == 201, saved.text
    assert "\r" not in saved.json()["body"]
    assert saved.json()["body"] == "第一段\n\n第二段"


async def test_version_numbering_is_per_store(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """多分店（§4）：A 店改自己的，B 店讀到的仍是自己的那份、版本號不受影響。"""
    mgr_a, _, _ = await _seed(db_session, name="A 店")
    mgr_b, _, store_b = await _seed(db_session, name="B 店")
    base = (await client.get("/api/v1/agreements/current", headers=_auth(mgr_a))).json()["version"]

    await client.post(
        "/api/v1/agreements",
        json={"title": "A 店版", "body": "只有 A 店", "expected_version": base},
        headers=_auth(mgr_a),
    )
    b_current = (await client.get("/api/v1/agreements/current", headers=_auth(mgr_b))).json()
    assert b_current["version"] == base
    assert "只有 A 店" not in b_current["body"]

    b_saved = await client.post(
        "/api/v1/agreements",
        json={"title": "B 店版", "body": "只有 B 店", "expected_version": base},
        headers=_auth(mgr_b),
    )
    assert b_saved.json()["version"] == base + 1  # B 店自己從 base 往上加，不接著 A 店
    rows = (
        await db_session.scalars(
            select(AgreementVersion).where(AgreementVersion.store_id == store_b)
        )
    ).all()
    assert {row.version for row in rows} == {base, base + 1}


async def test_stale_edit_is_rejected_instead_of_silently_overwriting(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """兩人各開一份舊版編輯，後存的不得無聲蓋掉先存的（Codex 審查 P2）。

    版本號遞增擋不住這件事：B 拿舊內容存成「下一版」照樣成功，A 剛改好的內容就被
    蓋過去且沒有人發現。要求帶 expected_version，對不上就 409 請他重新載入。
    """
    mgr, _, _ = await _seed(db_session)
    base = (await client.get("/api/v1/agreements/current", headers=_auth(mgr))).json()

    first = await client.post(
        "/api/v1/agreements",
        json={"title": "A 改的", "body": "A 的內文", "expected_version": base["version"]},
        headers=_auth(mgr),
    )
    assert first.status_code == 201, first.text

    # B 手上還是 base 那一版
    stale = await client.post(
        "/api/v1/agreements",
        json={"title": "B 改的", "body": "B 的內文", "expected_version": base["version"]},
        headers=_auth(mgr),
    )
    assert stale.status_code == 409, stale.text

    current = (await client.get("/api/v1/agreements/current", headers=_auth(mgr))).json()
    assert current["body"] == "A 的內文"  # A 的內容還在


async def test_signed_task_keeps_the_text_the_seller_actually_saw(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """改版之後，先前簽過的那筆仍指向舊版全文（docs/23 §5 的核心）。

    這是整條功能的法律重點：日後有爭議時，拿得出來的必須是「客人當初螢幕上那一份」，
    不是店家後來改成的版本。
    """
    from app.core.crypto import get_pii_cipher, national_id_blind_index
    from app.modules.contacts.models import Contact
    from app.modules.signing.models import SignatureTask
    from app.shared.enums import PayoutMethod
    from tests.integration.customer_display_helpers import signed_affidavit

    mgr_token, _, store_id = await _seed(db_session)
    mgr_id = await db_session.scalar(select(User.id).where(User.store_id == store_id))
    assert mgr_id is not None
    national_id = "A123456789"
    seller = Contact(
        store_id=store_id,
        name="賣方",
        roles=["SELLER"],
        address="台北市測試路 1 號",
        national_id_enc=get_pii_cipher().encrypt(national_id),
        national_id_blind_index=national_id_blind_index(national_id),
    )
    db_session.add(seller)
    await db_session.flush()

    before = (await client.get("/api/v1/agreements/current", headers=_auth(mgr_token))).json()
    task_id = await signed_affidavit(
        db_session,
        store_id=store_id,
        contact_id=seller.id,
        actor_user_id=mgr_id,
        content={"items": [{"name": "帳篷", "amount": "1000"}], "total": "1000"},
        payout=PayoutMethod.CASH,
    )

    published = await client.post(
        "/api/v1/agreements",
        json={
            "title": "改過的標題",
            "body": "改過的內文",
            "expected_version": before["version"],
        },
        headers=_auth(mgr_token),
    )
    assert published.status_code == 201, published.text

    task = await db_session.get(SignatureTask, task_id)
    assert task is not None
    bound = await db_session.get(AgreementVersion, task.agreement_version_id)
    assert bound is not None
    assert bound.version == before["version"]
    assert bound.body == before["body"]  # 客人當初看到的那一份，一字未改
    assert bound.body != "改過的內文"
