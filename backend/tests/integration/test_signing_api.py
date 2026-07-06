"""K2 手持簽署 API 整合測試（docs/23）。

- RBAC 圍堵雙向（D4）：KIOSK 打一般店務端點 403（中央預設拒絕）；店務角色打 /kiosk 403。
- 任務狀態機：PENDING→SIGNED / CANCELLED；終態不可再簽/再作廢。
- 簽名守衛：base64 PNG magic、大小上限；AFFIDAVIT 撥款二選一（D7）、他類不得帶。
- 切結書版本：AFFIDAVIT 建立時 lazy 落庫 v1 並綁定；手持端附全文。
- 跨店隔離：他店任務不可見。
"""

import base64
from collections.abc import AsyncGenerator

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.signing.models import AgreementVersion
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole

# 1x1 透明 PNG（有效 magic + 完整結構）
_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


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


class Seeded:
    def __init__(self, store_id: int, contact_id: int, mgr: str, clerk: str, kiosk: str) -> None:
        self.store_id = store_id
        self.contact_id = contact_id
        self.mgr = mgr
        self.clerk = clerk
        self.kiosk = kiosk


async def _seed(session: AsyncSession) -> Seeded:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    kiosk = User(store_id=store.id, username="pad", password_hash="h", role=UserRole.KIOSK)
    contact = Contact(store_id=store.id, name="王小明", phone="0912345678", roles=["SELLER"])
    session.add_all([mgr, clerk, kiosk, contact])
    await session.flush()
    # 先落地：router 的錯誤路徑會 rollback，未 commit 的種子會被吃掉（savepoint 模式）。
    await session.commit()
    return Seeded(
        store.id,
        contact.id,
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        encode_access_token(user_id=kiosk.id, role="KIOSK", store_id=store.id),
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _task_payload(contact_id: int, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": "ACQUISITION_AFFIDAVIT",
        "contact_id": contact_id,
        "content": {
            "items": [{"name": "登山背包", "amount": "1200"}],
            "total": "1200",
            "seller_name": "王小明",
            "national_id_masked": "A12***678*",
        },
    }
    base.update(overrides)
    return base


async def _create_task(
    client: httpx.AsyncClient, token: str, contact_id: int, **overrides: object
) -> dict[str, object]:
    resp = await client.post(
        "/api/v1/signing/tasks", json=_task_payload(contact_id, **overrides), headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, object] = resp.json()
    return body


# ── RBAC 圍堵（docs/23 D4）─────────────────────────────────────────────


async def test_kiosk_blocked_from_general_endpoints(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """KIOSK 打一般店務端點 → 中央預設拒絕 403（一處圍堵，非逐端點防漏）。"""
    s = await _seed(db_session)
    for path in ("/api/v1/contacts?limit=10", "/api/v1/signing/tasks", "/api/v1/sales"):
        resp = await client.get(path, headers=_auth(s.kiosk))
        assert resp.status_code == 403, f"{path}: {resp.status_code} {resp.text}"
        assert "簽署" in resp.json()["detail"]


async def test_staff_blocked_from_kiosk_endpoints(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """店務角色（MANAGER/CLERK）打 /kiosk → 403（店務帳號不掛客人面向裝置）。"""
    s = await _seed(db_session)
    for token in (s.mgr, s.clerk):
        resp = await client.get("/api/v1/kiosk/tasks/current", headers=_auth(token))
        assert resp.status_code == 403, resp.text


async def test_kiosk_endpoints_require_auth(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    await _seed(db_session)
    resp = await client.get("/api/v1/kiosk/tasks/current")
    assert resp.status_code == 401


# ── 任務建立與切結書版本 ───────────────────────────────────────────────


async def test_create_affidavit_task_binds_agreement_v1(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await _seed(db_session)
    body = await _create_task(client, s.clerk, s.contact_id)
    assert body["status"] == "PENDING"
    assert body["kind"] == "ACQUISITION_AFFIDAVIT"
    assert body["agreement_version"] == 1
    assert body["has_signature"] is False
    assert body["chosen_payout"] is None
    # lazy 落庫恰好一列；再建一單不重複落庫
    await _create_task(client, s.clerk, s.contact_id)
    count = await db_session.scalar(select(func.count()).select_from(AgreementVersion))
    assert count == 1


async def test_create_task_unknown_contact_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await _seed(db_session)
    resp = await client.post(
        "/api/v1/signing/tasks", json=_task_payload(999999), headers=_auth(s.clerk)
    )
    assert resp.status_code == 404


async def test_non_affidavit_task_has_no_agreement(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await _seed(db_session)
    body = await _create_task(
        client, s.clerk, s.contact_id, kind="STORE_CREDIT_USE", content={"deduct": "300"}
    )
    assert body["agreement_version"] is None


# ── 手持端輪詢與全文 ───────────────────────────────────────────────────


async def test_kiosk_current_returns_latest_pending_with_agreement_text(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await _seed(db_session)
    resp = await client.get("/api/v1/kiosk/tasks/current", headers=_auth(s.kiosk))
    assert resp.status_code == 200
    assert resp.json() is None  # 無任務 → 待機

    first = await _create_task(client, s.clerk, s.contact_id)
    second = await _create_task(client, s.clerk, s.contact_id)
    resp = await client.get("/api/v1/kiosk/tasks/current", headers=_auth(s.kiosk))
    body = resp.json()
    assert body["id"] == second["id"] and body["id"] != first["id"]
    assert "切結書" in body["agreement_title"]
    assert "非贓物" in body["agreement_body"]
    assert "個人資料" in body["agreement_body"]


async def test_repush_cancels_previous_pending(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """重推＝舊單作廢：停在舊頁面的平板不可簽下舊快照（Codex 對抗式審查 high）。"""
    s = await _seed(db_session)
    first = await _create_task(client, s.clerk, s.contact_id)
    second = await _create_task(client, s.clerk, s.contact_id)

    resp = await client.get(f"/api/v1/signing/tasks/{first['id']}", headers=_auth(s.clerk))
    assert resp.json()["status"] == "CANCELLED"
    assert resp.json()["cancelled_at"] is not None

    # 舊任務不可再簽；新任務可簽
    resp = await client.post(
        f"/api/v1/kiosk/tasks/{first['id']}/sign",
        json={"signature_image_base64": _PNG_B64, "chosen_payout": "CASH"},
        headers=_auth(s.kiosk),
    )
    assert resp.status_code == 409
    resp = await client.post(
        f"/api/v1/kiosk/tasks/{second['id']}/sign",
        json={"signature_image_base64": _PNG_B64, "chosen_payout": "CASH"},
        headers=_auth(s.kiosk),
    )
    assert resp.status_code == 200


async def test_kiosk_cannot_read_finished_tasks(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """手持端不得憑 ID 枚舉歷史內容快照：已簽/已作廢一律 404（Codex 對抗式審查 high）。"""
    s = await _seed(db_session)
    signed = await _create_task(client, s.clerk, s.contact_id)
    resp = await client.post(
        f"/api/v1/kiosk/tasks/{signed['id']}/sign",
        json={"signature_image_base64": _PNG_B64, "chosen_payout": "CASH"},
        headers=_auth(s.kiosk),
    )
    assert resp.status_code == 200
    resp = await client.get(f"/api/v1/kiosk/tasks/{signed['id']}", headers=_auth(s.kiosk))
    assert resp.status_code == 404

    cancelled = await _create_task(client, s.clerk, s.contact_id)
    resp = await client.post(
        f"/api/v1/signing/tasks/{cancelled['id']}/cancel", headers=_auth(s.clerk)
    )
    assert resp.status_code == 200
    resp = await client.get(f"/api/v1/kiosk/tasks/{cancelled['id']}", headers=_auth(s.kiosk))
    assert resp.status_code == 404

    # PENDING 中仍可重讀（簽名頁確認未被作廢）
    pending = await _create_task(client, s.clerk, s.contact_id)
    resp = await client.get(f"/api/v1/kiosk/tasks/{pending['id']}", headers=_auth(s.kiosk))
    assert resp.status_code == 200
    # 店員端不受此限：歷史任務仍可查（對帳/列印）
    resp = await client.get(f"/api/v1/signing/tasks/{signed['id']}", headers=_auth(s.clerk))
    assert resp.status_code == 200


# ── 簽名流程與守衛 ─────────────────────────────────────────────────────


async def test_sign_affidavit_happy_path(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await _seed(db_session)
    task = await _create_task(client, s.clerk, s.contact_id)
    resp = await client.post(
        f"/api/v1/kiosk/tasks/{task['id']}/sign",
        json={"signature_image_base64": _PNG_B64, "chosen_payout": "CASH"},
        headers=_auth(s.kiosk),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "SIGNED"
    assert body["has_signature"] is True
    assert body["signed_at"] is not None
    assert body["chosen_payout"] == "CASH"

    # 已簽不可重簽（終態）
    resp = await client.post(
        f"/api/v1/kiosk/tasks/{task['id']}/sign",
        json={"signature_image_base64": _PNG_B64, "chosen_payout": "CASH"},
        headers=_auth(s.kiosk),
    )
    assert resp.status_code == 409

    # 店員可取回簽名 PNG 原圖（K6 列印用）
    resp = await client.get(f"/api/v1/signing/tasks/{task['id']}/signature", headers=_auth(s.clerk))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == base64.b64decode(_PNG_B64)


async def test_affidavit_payout_must_be_binary(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """D7：AFFIDAVIT 必選 CASH/STORE_CREDIT；缺省或 SPLIT 一律 422。"""
    s = await _seed(db_session)
    task = await _create_task(client, s.clerk, s.contact_id)
    for payout in (None, "SPLIT"):
        resp = await client.post(
            f"/api/v1/kiosk/tasks/{task['id']}/sign",
            json={"signature_image_base64": _PNG_B64, "chosen_payout": payout},
            headers=_auth(s.kiosk),
        )
        assert resp.status_code == 422, f"payout={payout}: {resp.text}"
    # 守衛失敗不改狀態
    resp = await client.get(f"/api/v1/signing/tasks/{task['id']}", headers=_auth(s.clerk))
    assert resp.json()["status"] == "PENDING"


async def test_non_affidavit_rejects_payout_choice(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await _seed(db_session)
    task = await _create_task(
        client, s.clerk, s.contact_id, kind="STORE_CREDIT_USE", content={"deduct": "300"}
    )
    resp = await client.post(
        f"/api/v1/kiosk/tasks/{task['id']}/sign",
        json={"signature_image_base64": _PNG_B64, "chosen_payout": "CASH"},
        headers=_auth(s.kiosk),
    )
    assert resp.status_code == 422
    resp = await client.post(
        f"/api/v1/kiosk/tasks/{task['id']}/sign",
        json={"signature_image_base64": _PNG_B64},
        headers=_auth(s.kiosk),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "SIGNED"


async def test_sign_rejects_bad_images(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    s = await _seed(db_session)
    task = await _create_task(client, s.clerk, s.contact_id)
    oversized = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 600_000).decode()
    magic = b"\x89PNG\r\n\x1a\n"
    iend = b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"
    # 只有 magic、無 IHDR/IEND 的偽 PNG
    fake_structure = base64.b64encode(magic + b"0" * 64).decode()
    # 結構完整但寬度為 0（不合理尺寸）
    zero_width = base64.b64encode(
        magic
        + b"\x00\x00\x00\x0dIHDR"
        + (0).to_bytes(4, "big")
        + (1).to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 4
        + iend
    ).decode()
    bad_payloads = [
        "not-base64!!!",  # 非法 base64
        base64.b64encode(b"GIF89a....").decode(),  # 非 PNG magic
        oversized,  # 超過大小上限（解碼前即以 base64 長度擋下）
        fake_structure,  # PNG magic 但無 IHDR/IEND
        zero_width,  # IHDR 尺寸不合理
    ]
    for payload in bad_payloads:
        resp = await client.post(
            f"/api/v1/kiosk/tasks/{task['id']}/sign",
            json={"signature_image_base64": payload, "chosen_payout": "CASH"},
            headers=_auth(s.kiosk),
        )
        assert resp.status_code == 422, resp.text


# ── 作廢（反悔機制）與狀態機 ───────────────────────────────────────────


async def test_cancel_then_sign_conflicts(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """店員作廢（客人反悔/改內容）→ 手持端再簽 409、再作廢 409。"""
    s = await _seed(db_session)
    task = await _create_task(client, s.clerk, s.contact_id)
    resp = await client.post(f"/api/v1/signing/tasks/{task['id']}/cancel", headers=_auth(s.clerk))
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"
    assert resp.json()["cancelled_at"] is not None

    resp = await client.post(
        f"/api/v1/kiosk/tasks/{task['id']}/sign",
        json={"signature_image_base64": _PNG_B64, "chosen_payout": "CASH"},
        headers=_auth(s.kiosk),
    )
    assert resp.status_code == 409
    resp = await client.post(f"/api/v1/signing/tasks/{task['id']}/cancel", headers=_auth(s.clerk))
    assert resp.status_code == 409

    # 作廢後手持端輪詢回到待機
    resp = await client.get("/api/v1/kiosk/tasks/current", headers=_auth(s.kiosk))
    assert resp.json() is None


async def test_signature_endpoint_404_before_signed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    s = await _seed(db_session)
    task = await _create_task(client, s.clerk, s.contact_id)
    resp = await client.get(f"/api/v1/signing/tasks/{task['id']}/signature", headers=_auth(s.clerk))
    assert resp.status_code == 404


# ── 跨店隔離 ───────────────────────────────────────────────────────────


async def test_cross_store_isolation(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    s = await _seed(db_session)
    task = await _create_task(client, s.clerk, s.contact_id)

    other = Store(name="他店")
    db_session.add(other)
    await db_session.flush()
    other_mgr = User(store_id=other.id, username="mgr2", password_hash="h", role=UserRole.MANAGER)
    other_kiosk = User(store_id=other.id, username="pad2", password_hash="h", role=UserRole.KIOSK)
    db_session.add_all([other_mgr, other_kiosk])
    await db_session.flush()
    await db_session.commit()
    mgr2 = encode_access_token(user_id=other_mgr.id, role="MANAGER", store_id=other.id)
    kiosk2 = encode_access_token(user_id=other_kiosk.id, role="KIOSK", store_id=other.id)

    resp = await client.get(f"/api/v1/signing/tasks/{task['id']}", headers=_auth(mgr2))
    assert resp.status_code == 404
    resp = await client.get("/api/v1/kiosk/tasks/current", headers=_auth(kiosk2))
    assert resp.json() is None
    resp = await client.post(
        f"/api/v1/kiosk/tasks/{task['id']}/sign",
        json={"signature_image_base64": _PNG_B64, "chosen_payout": "CASH"},
        headers=_auth(kiosk2),
    )
    assert resp.status_code == 404
