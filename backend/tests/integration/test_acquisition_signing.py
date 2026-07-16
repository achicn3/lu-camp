"""K4 收購×手持切結整合（docs/23）：收購綁定已簽切結、撥款一致、單次使用。"""

import base64
import itertools
import zlib
from collections.abc import AsyncGenerator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_pii_cipher, national_id_blind_index
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.acquisition.models import Acquisition
from app.modules.acquisition.service import AcquisitionService
from app.modules.contacts.models import Contact
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import PayoutMethod, SignatureTaskKind, UserRole
from app.shared.exceptions import AcquisitionRequiresNationalId, SignatureContentMismatch


def _signature_png(width: int = 200, height: int = 80) -> str:
    magic = b"\x89PNG\r\n\x1a\n"

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + ctype
            + data
            + zlib.crc32(ctype + data).to_bytes(4, "big")
        )

    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for _x in range(width):
            raw += b"\x00\x00\x00\xff" if 20 <= y <= 40 else b"\xff\xff\xff\xff"
    ihdr = width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00"
    png = (
        magic
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


_PNG = _signature_png()
_idem = itertools.count()


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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"k-{next(_idem)}"}


def _enc_contact(store_id: int, name: str, phone: str, nid: str, roles: list[str]) -> Contact:
    """建一個帶真加密 national_id 的聯絡人（供切結遮罩解密用）。"""
    return Contact(
        store_id=store_id,
        name=name,
        phone=phone,
        national_id_enc=get_pii_cipher().encrypt(nid),
        national_id_blind_index=national_id_blind_index(nid),
        roles=roles,
    )


async def _seed(session: AsyncSession) -> tuple[int, str, int]:
    """回 (store_id, clerk_token, seller_contact_id)；seller 有 national_id＋MEMBER。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    contact = _enc_contact(store.id, "賣家", "0912345678", "A123456789", ["SELLER", "MEMBER"])
    session.add_all([clerk, contact])
    await session.flush()
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    await session.commit()
    return store.id, token, contact.id


async def _signed_affidavit(
    session: AsyncSession, store_id: int, contact_id: int, clerk_id: int, payout: PayoutMethod
) -> int:
    """建立並簽署一張收購切結任務（內容＝相機/1800），回 task_id。"""
    svc = SigningService(session)
    task = await svc.create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
            contact_id=contact_id,
            content={"items": [{"name": "相機", "amount": "1800"}], "total": "1800"},
        ),
        created_by=clerk_id,
    )
    await svc.sign_task(store_id, task.id, signature_image_base64=_PNG, chosen_payout=payout)
    await session.commit()
    return task.id


def _buyout_body(
    contact_id: int, task_id: int, payout: str, cost: str = "1800"
) -> dict[str, object]:
    return {
        "type": "BUYOUT",
        "contact_id": contact_id,
        "items": [{"name": "相機", "grade": "A", "listed_price": "3000", "acquisition_cost": cost}],
        "payout_method": payout,
        "signature_task_id": task_id,
    }


async def _clerk_id(session: AsyncSession, store_id: int) -> int:
    return int(await session.scalar(select(User.id).where(User.store_id == store_id)) or 0)


async def test_acquisition_binds_signed_affidavit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    task_id = await _signed_affidavit(db_session, store_id, contact_id, clerk_id, PayoutMethod.CASH)

    resp = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    acq_id = resp.json()["acquisition_id"]
    linked = await db_session.scalar(select(Acquisition).where(Acquisition.id == acq_id))
    assert linked is not None and linked.signature_task_id == task_id


async def test_signature_lost_response_replays_same_acquisition(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """回應遺失重試（新冪等鍵、同一張收購）→ 回原結果重放、不建第二列（Codex K4 第九輪）。

    前端每次 POST 產新 Idempotency-Key，故無法以冪等鍵重放，會撞單次使用唯一約束；但因請求
    指紋相符，須回原收購讓前端跑成功路徑（取單號/開櫃），且 DB 仍只有一張收購、只撥款一次。
    """
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    task_id = await _signed_affidavit(db_session, store_id, contact_id, clerk_id, PayoutMethod.CASH)

    first = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=_auth(token)
    )
    assert first.status_code == 201, first.text
    acq_id = first.json()["acquisition_id"]
    # 不同冪等鍵、完全相同的請求 → 重放同一張，非 409、非第二列。
    retry = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=_auth(token)
    )
    assert retry.status_code == 201, retry.text
    assert retry.json()["acquisition_id"] == acq_id
    rows = (
        await db_session.scalars(
            select(Acquisition.id).where(Acquisition.signature_task_id == task_id)
        )
    ).all()
    assert list(rows) == [acq_id], rows


async def test_signature_replay_after_cash_session_closed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """首次已 commit、抽屜隨後關帳，回應遺失重試（新鍵）仍重放同一張（Codex K4 第十輪）。

    前置重放須**先於**開帳檢查：否則重試會先撞 NoOpenCashSession、永遠走不到重放，店員回不了
    成功路徑（單號/開櫃），而收購/撥款其實已發生。
    """
    store_id, token, contact_id = await _seed(db_session)
    opened = await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    assert opened.status_code in (200, 201), opened.text
    session_id = opened.json()["id"]
    clerk_id = await _clerk_id(db_session, store_id)
    task_id = await _signed_affidavit(db_session, store_id, contact_id, clerk_id, PayoutMethod.CASH)

    first = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=_auth(token)
    )
    assert first.status_code == 201, first.text
    acq_id = first.json()["acquisition_id"]
    # 關帳（抽屜關閉）——模擬回應遺失後、店員換人/交班關帳。
    closed = await client.post(
        f"/api/v1/cash-sessions/{session_id}/close",
        json={"counted_amount": "1000"},
        headers=_auth(token),
    )
    assert closed.status_code in (200, 201), closed.text
    # 新冪等鍵重試：即使已無開帳班別，仍以指紋重放同一張、非 NoOpenCashSession。
    retry = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=_auth(token)
    )
    assert retry.status_code == 201, retry.text
    assert retry.json()["acquisition_id"] == acq_id


async def test_signature_reuse_different_content_conflicts(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同一張切結被拿去綁**不同內容**的收購 → 409（單次使用未被重放路徑削弱）。

    第二張與切結的品名/金額/總額相符（過內容比對），但其他欄位（listed_price）不同 → 請求
    指紋不符，不得重放；維持 409，一張切結只能綁其原本那張收購。
    """
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    task_id = await _signed_affidavit(db_session, store_id, contact_id, clerk_id, PayoutMethod.CASH)

    first = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=_auth(token)
    )
    assert first.status_code == 201, first.text
    other = _buyout_body(contact_id, task_id, "CASH")
    other["items"] = [
        {"name": "相機", "grade": "A", "listed_price": "3500", "acquisition_cost": "1800"}
    ]
    second = await client.post("/api/v1/acquisitions", json=other, headers=_auth(token))
    assert second.status_code == 409, second.text


async def test_payout_must_match_affidavit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    # 切結所選 STORE_CREDIT，但收購送 CASH → 422 不一致
    task_id = await _signed_affidavit(
        db_session, store_id, contact_id, clerk_id, PayoutMethod.STORE_CREDIT
    )
    resp = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=_auth(token)
    )
    assert resp.status_code == 422, resp.text


async def test_affidavit_wrong_contact_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    # 另一位會員
    other = _enc_contact(store_id, "別人", "0955555555", "B123456780", ["SELLER"])
    db_session.add(other)
    await db_session.flush()
    await db_session.commit()
    task_id = await _signed_affidavit(db_session, store_id, other.id, clerk_id, PayoutMethod.CASH)
    # 收購對象是 contact_id，切結卻是 other → 422
    resp = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=_auth(token)
    )
    assert resp.status_code == 422, resp.text


async def test_acquisition_content_must_match_affidavit(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """簽了 1800 卻改送 5000 → 422：客人簽的必須就是這張收購（Codex K4 high）。"""
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    task_id = await _signed_affidavit(db_session, store_id, contact_id, clerk_id, PayoutMethod.CASH)
    # 內容一致 → 201；改金額 → 422
    okr = await client.post(
        "/api/v1/acquisitions",
        json=_buyout_body(contact_id, task_id, "CASH", cost="1800"),
        headers=_auth(token),
    )
    assert okr.status_code == 201, okr.text
    task2 = await _signed_affidavit(db_session, store_id, contact_id, clerk_id, PayoutMethod.CASH)
    bad = await client.post(
        "/api/v1/acquisitions",
        json=_buyout_body(contact_id, task2, "CASH", cost="5000"),
        headers=_auth(token),
    )
    assert bad.status_code == 422, bad.text


async def test_consignment_rejects_signature_task(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """寄售不支援手持切結綁定：帶已簽買斷切結的 CONSIGNMENT → 422（Codex K4 第二輪）。"""
    store_id, token, contact_id = await _seed(db_session)
    clerk_id = await _clerk_id(db_session, store_id)
    task_id = await _signed_affidavit(db_session, store_id, contact_id, clerk_id, PayoutMethod.CASH)
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "CONSIGNMENT",
            "contact_id": contact_id,
            "items": [{"name": "相機", "grade": "A", "listed_price": "3000", "commission_pct": 50}],
            "signature_task_id": task_id,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_fractional_signed_amount_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """切結金額為小數/缺 amount → 內容不符 422（Codex K4 第三輪：不可截斷/預設）。"""
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    svc = SigningService(db_session)
    # 切結金額 1800.9（小數）：K5 第八輪起於**建立時**即被深度 canonical 擋下（422），
    # 不再等到綁定——小數金額根本簽不出來（比原「綁定時 422」更強的守衛）。
    with pytest.raises(SignatureContentMismatch):
        await svc.create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
                contact_id=contact_id,
                content={"items": [{"name": "相機", "amount": "1800.9"}], "total": "1800.9"},
            ),
            created_by=clerk_id,
        )


async def test_affidavit_content_enriched_with_premium(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """AFFIDAVIT 任務後端補齊購物金溢價預覽（客人選購物金可多得幾%；使用者裁示）。"""
    store_id, _token, contact_id = await _seed(db_session)
    clerk_id = await _clerk_id(db_session, store_id)
    task = await SigningService(db_session).create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
            contact_id=contact_id,
            content={"items": [{"name": "相機", "amount": "1000"}], "total": "1000"},
        ),
        created_by=clerk_id,
    )
    await db_session.commit()
    prem = task.content.get("store_credit_premium")
    assert isinstance(prem, dict)
    # 預設溢價率 10% → 1000 現金等值 → 購物金 1100（多得 100）
    assert prem["amount"] == "1100" and prem["extra"] == "100"
    # 身分欄以後端會員檔為準補齊（D1）
    assert task.content.get("seller_name") == "賣家"


async def test_require_affidavit_setting_blocks_unsigned(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """開啟 require_acquisition_affidavit 後，付現收購未帶手持切結 → 422（D2）。"""
    from app.modules.settings.schemas import SettingsUpdateRequest
    from app.modules.settings.service import StoreSettingsService

    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    await StoreSettingsService(db_session).update_settings(
        store_id,
        actor_user_id=None,
        patch=SettingsUpdateRequest(require_acquisition_affidavit=True),
    )
    await db_session.commit()
    body = {
        "type": "BUYOUT",
        "contact_id": contact_id,
        "items": [
            {"name": "相機", "grade": "A", "listed_price": "3000", "acquisition_cost": "1800"}
        ],
        "payout_method": "CASH",
    }
    resp = await client.post("/api/v1/acquisitions", json=body, headers=_auth(token))
    assert resp.status_code == 422, resp.text


async def test_store_credit_premium_frozen_at_signing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """簽署後店長改溢價率，購物金入帳仍以簽署當下費率為準（Codex K4 第五輪 high）。"""
    from decimal import Decimal

    from app.modules.settings.schemas import SettingsUpdateRequest
    from app.modules.settings.service import StoreSettingsService
    from app.modules.storecredit.service import StoreCreditService

    store_id, token, contact_id = await _seed(db_session)  # 預設溢價 10%
    clerk_id = await _clerk_id(db_session, store_id)
    # 客人於手持端選購物金並簽署（切結快照凍結當下 10% 溢價）
    task_id = await _signed_affidavit(
        db_session, store_id, contact_id, clerk_id, PayoutMethod.STORE_CREDIT
    )
    # 店長事後把溢價率改成 20%
    await StoreSettingsService(db_session).update_settings(
        store_id, actor_user_id=None, patch=SettingsUpdateRequest(premium_rate=Decimal("0.20"))
    )
    await db_session.commit()
    # 完成購物金收購（切結金額 1800）
    resp = await client.post(
        "/api/v1/acquisitions",
        json=_buyout_body(contact_id, task_id, "STORE_CREDIT"),
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    # 入帳＝1800×(1+10%)=1980（凍結費率），非 20% 的 2160
    balance = await StoreCreditService(db_session).get_balance(store_id, contact_id)
    assert balance == Decimal(1980), balance


async def test_affidavit_requires_national_id_at_signing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """對象無可用身分證字號時，推送收購切結任務即被擋（Codex K4 第六輪 high）。"""
    store = Store(name="門市")
    db_session.add(store)
    await db_session.flush()
    clerk = User(store_id=store.id, username="c2", password_hash="h", role=UserRole.CLERK)
    no_id = Contact(store_id=store.id, name="無證號", phone="0911111111", roles=["MEMBER"])
    db_session.add_all([clerk, no_id])
    await db_session.flush()
    await db_session.commit()
    with pytest.raises(AcquisitionRequiresNationalId):
        await SigningService(db_session).create_task(
            store.id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
                contact_id=no_id.id,
                content={"items": [{"name": "x", "amount": "100"}], "total": "100"},
            ),
            created_by=clerk.id,
        )


async def test_identity_change_after_signing_blocks_binding(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """簽署後改了證號，綁定時身分不符 → 422（Codex K4 第六輪 high）。"""
    from app.core.crypto import get_pii_cipher as _c
    from app.core.crypto import national_id_blind_index as _b

    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    task_id = await _signed_affidavit(db_session, store_id, contact_id, clerk_id, PayoutMethod.CASH)
    # 簽署後把證號改掉（遮罩會變）
    contact = await db_session.scalar(select(Contact).where(Contact.id == contact_id))
    assert contact is not None
    contact.national_id_enc = _c().encrypt("Z223456781")
    contact.national_id_blind_index = _b("Z223456781")
    await db_session.commit()
    resp = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=_auth(token)
    )
    assert resp.status_code == 422, resp.text


async def test_same_mask_id_change_blocks_binding(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """簽署後改成「同遮罩、不同證號」（A123456789→A129999789 皆 A12****789），指紋不同
    仍應擋下綁定（Codex K4 第七輪 high：遮罩有損、須以穩定指紋比對）。"""
    store_id, token, contact_id = await _seed(db_session)  # A123456789
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    task_id = await _signed_affidavit(db_session, store_id, contact_id, clerk_id, PayoutMethod.CASH)
    contact = await db_session.scalar(select(Contact).where(Contact.id == contact_id))
    assert contact is not None
    # 同遮罩（A12****789）但不同證號
    contact.national_id_enc = get_pii_cipher().encrypt("A129999789")
    contact.national_id_blind_index = national_id_blind_index("A129999789")
    await db_session.commit()
    resp = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=_auth(token)
    )
    assert resp.status_code == 422, resp.text


async def _signed_bulk_affidavit(
    session: AsyncSession,
    store_id: int,
    contact_id: int,
    clerk_id: int,
    *,
    total_qty: int = 10,
    basis: str = "BAG",
) -> int:
    """建立並簽署一張**散裝批**收購切結（含數量/基準快照），回 task_id。"""
    svc = SigningService(session)
    task = await svc.create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
            contact_id=contact_id,
            content={
                "items": [{"name": "雜書一批", "amount": "1800"}],
                "total": "1800",
                "lot": {"total_qty": total_qty, "acquisition_basis": basis},
            },
        ),
        created_by=clerk_id,
    )
    await svc.sign_task(
        store_id, task.id, signature_image_base64=_PNG, chosen_payout=PayoutMethod.CASH
    )
    await session.commit()
    return task.id


def _bulk_body(
    contact_id: int, task_id: int, *, total_qty: int = 10, basis: str = "BAG"
) -> dict[str, object]:
    return {
        "type": "BULK_LOT",
        "contact_id": contact_id,
        "lot": {
            "name": "雜書一批",
            "acquisition_cost": "1800",
            "acquisition_basis": basis,
            "total_qty": total_qty,
            "unit_price": "50",
        },
        "payout_method": "CASH",
        "signature_task_id": task_id,
    }


async def test_bulk_affidavit_binds_signed_quantity(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """散裝批：簽了 10 件卻改送 20 件（同名同總額同撥款）→ 422（Codex K4 第十一輪 high）。

    數量納入簽署快照精確比對——否則會建出客人未確認數量的存貨。相同數量則 201。
    """
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    task_id = await _signed_bulk_affidavit(db_session, store_id, contact_id, clerk_id, total_qty=10)
    # 改數量 → 422
    bad = await client.post(
        "/api/v1/acquisitions",
        json=_bulk_body(contact_id, task_id, total_qty=20),
        headers=_auth(token),
    )
    assert bad.status_code == 422, bad.text
    # 相同數量 → 201
    task2 = await _signed_bulk_affidavit(db_session, store_id, contact_id, clerk_id, total_qty=10)
    ok = await client.post(
        "/api/v1/acquisitions",
        json=_bulk_body(contact_id, task2, total_qty=10),
        headers=_auth(token),
    )
    assert ok.status_code == 201, ok.text


async def test_bulk_affidavit_binds_basis(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """散裝批：簽了 BAG 卻改送 WEIGHT（同名同量同總額）→ 422（計價基準納入快照）。"""
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    task_id = await _signed_bulk_affidavit(db_session, store_id, contact_id, clerk_id, basis="BAG")
    bad = await client.post(
        "/api/v1/acquisitions",
        json=_bulk_body(contact_id, task_id, basis="WEIGHT"),
        headers=_auth(token),
    )
    assert bad.status_code == 422, bad.text


async def test_kiosk_api_never_exposes_identity_fingerprint(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """手持端 API 回應不得含綁定用身分指紋（national_id_blind_index）（Codex K4 第十一輪 medium）。

    指紋為 HMAC、可跨任務關聯身分——即使前端只是視覺隱藏，API 也絕不能回傳。改存後端內部欄後，
    /kiosk/tasks/{id} 的 content 不再含該值。
    """
    store_id, _token, contact_id = await _seed(db_session)
    clerk_id = await _clerk_id(db_session, store_id)
    # 建 KIOSK 帳號取手持端 token
    kiosk = User(store_id=store_id, username="pad", password_hash="h", role=UserRole.KIOSK)
    db_session.add(kiosk)
    await db_session.flush()
    kiosk_token = encode_access_token(user_id=kiosk.id, role="KIOSK", store_id=store_id)
    await db_session.commit()
    task_id = await _signed_affidavit(db_session, store_id, contact_id, clerk_id, PayoutMethod.CASH)

    # 身分指紋的實際 HMAC 值（絕不該出現在回應任何角落）
    fp = national_id_blind_index("A123456789")
    resp = await client.get(
        f"/api/v1/kiosk/tasks/{task_id}", headers={"Authorization": f"Bearer {kiosk_token}"}
    )
    # 已簽任務手持端不再可取（get_pending_task_for_kiosk 僅 PENDING）→ 用 pending 任務驗證
    if resp.status_code != 200:
        task2 = await SigningService(db_session).create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
                contact_id=contact_id,
                content={"items": [{"name": "相機", "amount": "1800"}], "total": "1800"},
            ),
            created_by=clerk_id,
        )
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/kiosk/tasks/{task2.id}",
            headers={"Authorization": f"Bearer {kiosk_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "national_id_fingerprint" not in body["content"]
    assert "identity_fingerprint" not in body
    assert fp not in resp.text


async def test_injected_fingerprint_key_scrubbed_from_content(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """客端於 content 夾帶 national_id_fingerprint 會被剝除、不入儲存/回應；綁定用指紋改由
    後端內部欄以會員檔擷取（Codex K4 第十二輪 high：防注入/回顯、版本銜接一致）。"""
    store_id, _token, contact_id = await _seed(db_session)  # A123456789
    clerk_id = await _clerk_id(db_session, store_id)
    task = await SigningService(db_session).create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
            contact_id=contact_id,
            content={
                "items": [{"name": "相機", "amount": "1800"}],
                "total": "1800",
                "national_id_fingerprint": "ATTACKER-INJECTED",
            },
        ),
        created_by=clerk_id,
    )
    await db_session.commit()
    # 注入鍵被剝除、不入 content（不會經 API 回顯）
    assert "national_id_fingerprint" not in task.content
    # 綁定用指紋以後端內部欄、取自會員檔（非客端注入值）
    assert task.identity_fingerprint == national_id_blind_index("A123456789")


async def test_signature_replay_excludes_voided_acquisition(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """回應遺失後店員已作廢該收購，重試（新鍵、同切結、同內容）不得回放成功→ 409（第十三輪）。

    否則前端會為已反轉（作廢）的收購又開櫃/印標，與帳本脫節。該切結已被消耗（單次使用已用掉），
    需重新推送簽署。
    """
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "5000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    task_id = await _signed_affidavit(db_session, store_id, contact_id, clerk_id, PayoutMethod.CASH)

    first = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=_auth(token)
    )
    assert first.status_code == 201, first.text
    acq_id = first.json()["acquisition_id"]

    # 店員作廢該收購（對稱反轉現金/庫存）
    await AcquisitionService(db_session).void_acquisition(
        store_id, acq_id, actor_user_id=clerk_id, reason="回應遺失後人工作廢"
    )
    await db_session.commit()

    # 新冪等鍵重試同一份：不得回放已作廢收購為成功
    retry = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=_auth(token)
    )
    assert retry.status_code == 409, retry.text


async def test_signature_same_key_replay_excludes_voided(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """作廢後以**原冪等鍵**重試（前端凍結鍵跨模糊失敗）也不得回放已作廢收購 → 409（第十四輪）。"""
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "5000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    task_id = await _signed_affidavit(db_session, store_id, contact_id, clerk_id, PayoutMethod.CASH)
    # 固定冪等鍵（模擬前端凍結）
    hdr = {"Authorization": f"Bearer {token}", "Idempotency-Key": "frozen-void-key"}

    first = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=hdr
    )
    assert first.status_code == 201, first.text
    acq_id = first.json()["acquisition_id"]

    await AcquisitionService(db_session).void_acquisition(
        store_id, acq_id, actor_user_id=clerk_id, reason="回應遺失後人工作廢"
    )
    await db_session.commit()

    # 同一把冪等鍵、同一份 → 不得回放已作廢收購為成功
    retry = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=hdr
    )
    assert retry.status_code == 409, retry.text


async def test_unsigned_voided_replay_rejected_same_key(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """未帶切結（政策預設關）的現金收購作廢後，以原冪等鍵重試也不得回放成功 → 409（第十六輪）。

    這是預設主要路徑：否則作廢後重試會又開櫃/印標，與已反轉帳本脫節。
    """
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "5000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    unsigned = {
        "type": "BUYOUT",
        "contact_id": contact_id,
        "items": [
            {"name": "相機", "grade": "A", "listed_price": "3000", "acquisition_cost": "1800"}
        ],
        "payout_method": "CASH",
    }
    hdr = {"Authorization": f"Bearer {token}", "Idempotency-Key": "unsigned-void-key"}

    first = await client.post("/api/v1/acquisitions", json=unsigned, headers=hdr)
    assert first.status_code == 201, first.text
    acq_id = first.json()["acquisition_id"]

    await AcquisitionService(db_session).void_acquisition(
        store_id, acq_id, actor_user_id=clerk_id, reason="作廢後重試"
    )
    await db_session.commit()

    retry = await client.post("/api/v1/acquisitions", json=unsigned, headers=hdr)
    assert retry.status_code == 409, retry.text


async def test_unsigned_changed_payload_same_key_conflicts(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """未簽收購已提交後、以**原冪等鍵**送不同內容 → 409（第十七輪）：前端據此不可丟棄鍵改新鍵
    重送（否則重複建單/撥款）。"""
    _store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "5000"}, headers=_auth(token)
    )
    base_items: list[dict[str, object]] = [
        {"name": "相機", "grade": "A", "listed_price": "3000", "acquisition_cost": "1800"}
    ]
    base: dict[str, object] = {
        "type": "BUYOUT",
        "contact_id": contact_id,
        "items": base_items,
        "payout_method": "CASH",
    }
    hdr = {"Authorization": f"Bearer {token}", "Idempotency-Key": "unsigned-changed-key"}

    first = await client.post("/api/v1/acquisitions", json=base, headers=hdr)
    assert first.status_code == 201, first.text
    # 同鍵、改內容（金額變）→ 指紋不符 → 409（先前已提交的證據）
    changed = {**base, "items": [{**base_items[0], "acquisition_cost": "2500"}]}
    conflict = await client.post("/api/v1/acquisitions", json=changed, headers=hdr)
    assert conflict.status_code == 409, conflict.text


async def test_lot_bearing_affidavit_cannot_bind_buyout(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """含 lot 敘述（散裝件數/基準）的切結不得綁到 BUYOUT → 422（Codex K5 第九輪：
    客人簽的每個欄位都必須被綁定驗證，BUYOUT 不驗 lot → fail closed）。"""
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "5000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    svc = SigningService(db_session)
    task = await svc.create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
            contact_id=contact_id,
            content={
                "items": [{"name": "相機", "amount": "1800"}],
                "total": "1800",
                "lot": {"total_qty": 10, "acquisition_basis": "BAG"},
            },
        ),
        created_by=clerk_id,
    )
    await svc.sign_task(
        store_id, task.id, signature_image_base64=_PNG, chosen_payout=PayoutMethod.CASH
    )
    await db_session.commit()
    # 品名/金額/總額都相符的 BUYOUT，但切結含 lot → 422
    resp = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task.id, "CASH"), headers=_auth(token)
    )
    assert resp.status_code == 422, resp.text


async def test_signature_task_detail_backfills_bound_acquisition(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """調閱端點反向回填綁定收購單（任務先建、收購後綁——ref_id 不會有值）。"""
    store_id, token, contact_id = await _seed(db_session)
    await client.post(
        "/api/v1/cash-sessions/open", json={"opening_float": "1000"}, headers=_auth(token)
    )
    clerk_id = await _clerk_id(db_session, store_id)
    task_id = await _signed_affidavit(db_session, store_id, contact_id, clerk_id, PayoutMethod.CASH)

    resp = await client.post(
        "/api/v1/acquisitions", json=_buyout_body(contact_id, task_id, "CASH"), headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    acq_id = resp.json()["acquisition_id"]

    detail = await client.get(f"/api/v1/signing/tasks/{task_id}", headers=_auth(token))
    assert detail.status_code == 200
    body = detail.json()
    assert body["bound_acquisition_id"] == acq_id
    assert body["bound_sale_id"] is None
