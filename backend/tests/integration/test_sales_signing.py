"""K5 結帳×購物金扣抵手持簽署整合（docs/23 D3）：綁定已簽 STORE_CREDIT_USE、折抵額精確比對、
單次使用、政策強制、內容補齊（本次折抵/剩餘）。
"""

import base64
import itertools
import zlib
from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import CatalogProduct
from app.modules.sales.models import Sale
from app.modules.settings.schemas import SettingsUpdateRequest
from app.modules.settings.service import StoreSettingsService
from app.modules.signing.schemas import SignatureTaskCreate
from app.modules.signing.service import SigningService
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import (
    PayoutMethod,
    SignatureTaskKind,
    StoreCreditSourceType,
    UserRole,
)


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
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": f"sale-k5-{next(_idem)}"}


async def _seed(session: AsyncSession) -> tuple[str, int, int]:
    """建店+店員（開帳），回 (clerk_token, store_id, clerk_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, store.id, clerk.id


async def _seed_catalog(session: AsyncSession, store_id: int, *, price: str, qty: int) -> int:
    product = CatalogProduct(
        store_id=store_id, sku="SKU1", name="飲料", unit_price=Decimal(price), quantity_on_hand=qty
    )
    session.add(product)
    await session.flush()
    return product.id


async def _seed_member_with_credit(
    session: AsyncSession, store_id: int, clerk_id: int, balance: int
) -> int:
    """建會員並以完整背書收購入帳 balance（premium 0 → 餘額 = balance）。"""
    member = Contact(store_id=store_id, name="會員", roles=["MEMBER"], national_id_enc="enc")
    session.add(member)
    await session.flush()
    acq_id = await session.scalar(
        text(
            "INSERT INTO acquisitions"
            " (store_id, type, contact_id, clerk_user_id, total_cash_paid,"
            "  payout_method, payout_cash_amount, payout_credit_cash_equivalent,"
            "  created_at, updated_at)"
            " VALUES (:sid, 'BUYOUT', :cid, :uid, 0, 'STORE_CREDIT', 0, :amt,"
            "  now(), now()) RETURNING id"
        ),
        {"sid": store_id, "cid": member.id, "uid": clerk_id, "amt": balance},
    )
    await session.execute(
        text(
            "INSERT INTO serialized_items"
            " (store_id, item_code, name, grade, ownership_type, acquisition_cost,"
            "  listed_price, acquisition_id, created_at, updated_at)"
            " VALUES (:sid, :code, '收購品', 'A', 'OWNED', :amt, :amt, :aid, now(), now())"
        ),
        {"sid": store_id, "code": f"K5-CRED-{member.id}", "amt": balance, "aid": acq_id},
    )
    await StoreCreditService(session).credit(
        store_id,
        member.id,
        cash_equivalent=Decimal(balance),
        premium_rate=Decimal("0"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=acq_id,
        created_by=clerk_id,
    )
    return member.id


async def _signed_use_task(
    session: AsyncSession,
    store_id: int,
    contact_id: int,
    clerk_id: int,
    *,
    debit: str,
    sale_total: str | None = None,
) -> int:
    """建立並簽署一張購物金扣抵確認任務（content：本次折抵＋消費合計），回 task_id。

    sale_total 預設＝debit（純購物金結帳）；混合收款測試另傳實際合計。
    """
    svc = SigningService(session)
    task = await svc.create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.STORE_CREDIT_USE,
            contact_id=contact_id,
            content={"debit": debit, "sale_total": sale_total if sale_total is not None else debit},
        ),
        created_by=clerk_id,
    )
    await svc.sign_task(store_id, task.id, signature_image_base64=_PNG, chosen_payout=None)
    await session.commit()
    return task.id


def _sale_body(
    product_id: int, member_id: int, task_id: int | None, *, credit: str, cash: str | None = None
) -> dict[str, object]:
    tenders: list[dict[str, str]] = []
    if cash is not None:
        tenders.append({"tender_type": "CASH", "amount": cash})
    tenders.append({"tender_type": "STORE_CREDIT", "amount": credit})
    body: dict[str, object] = {
        "lines": [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 1}],
        "buyer_contact_id": member_id,
        "tenders": tenders,
    }
    if task_id is not None:
        body["signature_task_id"] = task_id
    return body


async def test_store_credit_sale_binds_signed_task(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已簽扣抵確認（debit=300）＋購物金 300 結帳 → 201 且 sale 綁定該任務、餘額扣 300。"""
    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    task_id = await _signed_use_task(db_session, store_id, member_id, clerk_id, debit="300")

    resp = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, task_id, credit="300"),
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    sale_id = resp.json()["id"]
    sale = await db_session.scalar(select(Sale).where(Sale.id == sale_id))
    assert sale is not None and sale.signature_task_id == task_id
    balance = await StoreCreditService(db_session).get_balance(store_id, member_id)
    assert balance == Decimal("700")


async def test_signed_debit_must_match_checkout(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """簽了折抵 300 卻結帳折 200（現金補 100）→ 422：客人簽的必須就是這次扣抵。"""
    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    task_id = await _signed_use_task(db_session, store_id, member_id, clerk_id, debit="300")

    resp = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, task_id, credit="200", cash="100"),
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_signature_task_single_use(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同一張扣抵簽署綁第二筆結帳 → 409（單次使用）。"""
    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    task_id = await _signed_use_task(db_session, store_id, member_id, clerk_id, debit="300")

    first = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, task_id, credit="300"),
        headers=_auth(token),
    )
    assert first.status_code == 201, first.text
    # 同簽署綁**不同購物車**（同額、量 2 現金補差）→ 指紋不符 → 409。
    # 註：同簽署＋完全相同購物車＝回應遺失的安全回放（回原單、非 409），
    # 見 test_signature_lost_response_replays_same_sale（Codex K5 第一輪語意）。
    other = {
        "lines": [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 2}],
        "buyer_contact_id": member_id,
        "tenders": [
            {"tender_type": "CASH", "amount": "300"},
            {"tender_type": "STORE_CREDIT", "amount": "300"},
        ],
        "signature_task_id": task_id,
    }
    second = await client.post("/api/v1/sales", json=other, headers=_auth(token))
    assert second.status_code == 409, second.text


async def test_wrong_member_task_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """扣抵簽署對象是別的會員 → 422。"""
    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    other = Contact(store_id=store_id, name="別人", roles=["MEMBER"], national_id_enc="enc")
    db_session.add(other)
    await db_session.flush()
    await db_session.commit()
    task_id = await _signed_use_task(db_session, store_id, other.id, clerk_id, debit="300")

    resp = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, task_id, credit="300"),
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_require_setting_blocks_unsigned(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """require_store_credit_signing 開啟後，購物金結帳未帶簽署 → 422；純現金不受影響。"""
    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    await StoreSettingsService(db_session).update_settings(
        store_id,
        actor_user_id=None,
        patch=SettingsUpdateRequest(require_store_credit_signing=True),
    )
    await db_session.commit()

    unsigned = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, None, credit="300"),
        headers=_auth(token),
    )
    assert unsigned.status_code == 422, unsigned.text
    # 純現金不受政策影響
    cash_only = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 1}],
            "buyer_contact_id": member_id,
            "tenders": [{"tender_type": "CASH", "amount": "300"}],
        },
        headers=_auth(token),
    )
    assert cash_only.status_code == 201, cash_only.text


async def test_cash_only_sale_rejects_task(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """未以購物金付款卻帶扣抵簽署 → 422（不可掛名消耗簽署）。"""
    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    task_id = await _signed_use_task(db_session, store_id, member_id, clerk_id, debit="300")

    resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 1}],
            "buyer_contact_id": member_id,
            "tenders": [{"tender_type": "CASH", "amount": "300"}],
            "signature_task_id": task_id,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_affidavit_task_cannot_bind_sale(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """收購切結（AFFIDAVIT）不可拿來當扣抵確認用 → 422（kind 檢查）。"""
    from app.core.crypto import get_pii_cipher, national_id_blind_index

    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    # 補證號（AFFIDAVIT 建立需可解密證號）
    member = await db_session.scalar(select(Contact).where(Contact.id == member_id))
    assert member is not None
    member.national_id_enc = get_pii_cipher().encrypt("A123456789")
    member.national_id_blind_index = national_id_blind_index("A123456789")
    await db_session.flush()
    svc = SigningService(db_session)
    task = await svc.create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
            contact_id=member_id,
            content={"items": [{"name": "x", "amount": "300"}], "total": "300"},
        ),
        created_by=clerk_id,
    )
    await svc.sign_task(
        store_id, task.id, signature_image_base64=_PNG, chosen_payout=PayoutMethod.STORE_CREDIT
    )
    await db_session.commit()

    resp = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, task.id, credit="300"),
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text


async def test_use_task_content_enriched_with_balance(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """STORE_CREDIT_USE 任務後端補齊「本次折抵/折抵後剩餘」（客人手持端核對用）。"""
    _token, store_id, clerk_id = await _seed(db_session)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    task = await SigningService(db_session).create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.STORE_CREDIT_USE,
            contact_id=member_id,
            content={"debit": "300", "sale_total": "300"},
        ),
        created_by=clerk_id,
    )
    await db_session.commit()
    assert task.content["balance_before"] == "1000"
    assert task.content["balance_after"] == "700"
    assert task.content["seller_name"] == "會員"


async def test_signature_lost_response_replays_same_sale(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """回應遺失重試（新冪等鍵、同簽署、同購物車）→ 回原單回放、不建第二筆、不重複扣購物金
    （Codex K5 第一輪；同 K4 第九輪模式）。"""
    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    task_id = await _signed_use_task(db_session, store_id, member_id, clerk_id, debit="300")

    body = _sale_body(product_id, member_id, task_id, credit="300")
    first = await client.post("/api/v1/sales", json=body, headers=_auth(token))
    assert first.status_code == 201, first.text
    sale_id = first.json()["id"]

    # 新冪等鍵、完全相同的結帳 → 回放同一單（非 409、非第二筆）
    retry = await client.post("/api/v1/sales", json=body, headers=_auth(token))
    assert retry.status_code == 201, retry.text
    assert retry.json()["id"] == sale_id
    # 只扣一次購物金
    balance = await StoreCreditService(db_session).get_balance(store_id, member_id)
    assert balance == Decimal("700")


async def test_voided_sale_signature_retry_conflicts(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """首筆已作廢後，以同簽署（新鍵）重試 → 409 不回放（K4 第十三/十六輪同款）。"""
    from app.modules.sales.service import SalesService

    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    task_id = await _signed_use_task(db_session, store_id, member_id, clerk_id, debit="300")

    body = _sale_body(product_id, member_id, task_id, credit="300")
    first = await client.post("/api/v1/sales", json=body, headers=_auth(token))
    assert first.status_code == 201, first.text
    sale_id = first.json()["id"]

    svc = SalesService(db_session)
    sale = await svc.get_sale(store_id, sale_id)
    assert sale is not None
    await svc.void_sale(sale, actor_user_id=clerk_id)
    await db_session.commit()

    retry = await client.post("/api/v1/sales", json=body, headers=_auth(token))
    assert retry.status_code == 409, retry.text


async def test_voided_sale_same_key_retry_conflicts(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已作廢的銷售以**原冪等鍵**重試也不得回放為成功 → 409（K4 第十四/十六輪同款）。"""
    from app.modules.sales.service import SalesService

    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    task_id = await _signed_use_task(db_session, store_id, member_id, clerk_id, debit="300")

    body = _sale_body(product_id, member_id, task_id, credit="300")
    hdr = {"Authorization": f"Bearer {token}", "Idempotency-Key": "k5-void-same-key"}
    first = await client.post("/api/v1/sales", json=body, headers=hdr)
    assert first.status_code == 201, first.text

    svc = SalesService(db_session)
    sale = await svc.get_sale(store_id, first.json()["id"])
    assert sale is not None
    await svc.void_sale(sale, actor_user_id=clerk_id)
    await db_session.commit()

    retry = await client.post("/api/v1/sales", json=body, headers=hdr)
    assert retry.status_code == 409, retry.text


async def test_signed_total_must_match_checkout(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同折抵額、換更大購物車（現金補差）→ 422：客人簽的合計必須就是這筆交易
    （Codex K5 第二輪 high：簽名證據不得描述不同的交易脈絡）。"""
    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    # 客人簽的是「折抵 300／合計 300」
    task_id = await _signed_use_task(db_session, store_id, member_id, clerk_id, debit="300")

    # 結帳卻是 600（量 2）＝現金 300＋購物金 300：折抵額相同、合計不同 → 422
    bigger = {
        "lines": [{"line_type": "CATALOG", "catalog_product_id": product_id, "qty": 2}],
        "buyer_contact_id": member_id,
        "tenders": [
            {"tender_type": "CASH", "amount": "300"},
            {"tender_type": "STORE_CREDIT", "amount": "300"},
        ],
        "signature_task_id": task_id,
    }
    resp = await client.post("/api/v1/sales", json=bigger, headers=_auth(token))
    assert resp.status_code == 422, resp.text
    # 未產生任何收款副作用：餘額原封不動
    balance = await StoreCreditService(db_session).get_balance(store_id, member_id)
    assert balance == Decimal("1000")


async def test_transaction_ack_content_canonicalized_from_sale(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """交易紀錄簽收內容一律以後端銷售單重建：客端竄改的 content 全數丟棄
    （Codex K5 第三輪 high：不可讓客人簽下描述錯誤交易的證據）。"""
    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    sale_resp = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, None, credit="300"),
        headers=_auth(token),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale_id = sale_resp.json()["id"]

    # 客端夾帶竄改內容（金額/單號都是假的）→ 後端整份覆寫為銷售單實態
    task = await SigningService(db_session).create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.TRANSACTION_ACK,
            contact_id=member_id,
            content={"sale_ref": "#999999", "total": "1", "note": "tampered"},
            ref_type="sale",
            ref_id=sale_id,
        ),
        created_by=clerk_id,
    )
    await db_session.commit()
    assert task.content["sale_ref"] == f"#{sale_id}"
    assert task.content["total"] == "300"
    assert "note" not in task.content  # 客端敘述全數丟棄
    assert "purchased_at" in task.content


async def test_transaction_ack_requires_matching_buyer_and_live_sale(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """簽收須 ref 指向本店銷售、對象＝買方、非作廢；任一不符 → 422。"""
    import pytest

    from app.modules.sales.service import SalesService
    from app.shared.exceptions import SignatureContentMismatch

    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    other = Contact(store_id=store_id, name="別人", roles=["MEMBER"], national_id_enc="enc")
    db_session.add(other)
    await db_session.flush()
    sale_resp = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, None, credit="300"),
        headers=_auth(token),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale_id = sale_resp.json()["id"]
    svc = SigningService(db_session)

    def ack(contact_id: int, ref_type: str | None, ref_id: int | None) -> SignatureTaskCreate:
        return SignatureTaskCreate(
            kind=SignatureTaskKind.TRANSACTION_ACK,
            contact_id=contact_id,
            content={},
            ref_type=ref_type,
            ref_id=ref_id,
        )

    # 無 ref → 拒
    with pytest.raises(SignatureContentMismatch):
        await svc.create_task(store_id, ack(member_id, None, None), created_by=clerk_id)
    # 對象非買方 → 拒
    with pytest.raises(SignatureContentMismatch):
        await svc.create_task(store_id, ack(other.id, "sale", sale_id), created_by=clerk_id)
    # 作廢後 → 拒
    sales = SalesService(db_session)
    sale = await sales.get_sale(store_id, sale_id)
    assert sale is not None
    await sales.void_sale(sale, actor_user_id=clerk_id)
    await db_session.flush()
    with pytest.raises(SignatureContentMismatch):
        await svc.create_task(store_id, ack(member_id, "sale", sale_id), created_by=clerk_id)


async def test_ack_sign_rejected_after_sale_voided_via_kiosk_api(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """推送簽收後、客人簽名前該單被作廢 → 手持端 API 簽名回 409 且任務**已提交**為 CANCELLED、
    不再被輪詢到（Codex K5 第四/五輪 high：作廢須提交、不得因 rollback 遺失）。"""
    from app.modules.sales.service import SalesService
    from app.shared.enums import SignatureTaskStatus

    token, store_id, clerk_id = await _seed(db_session)
    kiosk_user = User(store_id=store_id, username="pad", password_hash="h", role=UserRole.KIOSK)
    db_session.add(kiosk_user)
    await db_session.flush()
    kiosk_token = encode_access_token(user_id=kiosk_user.id, role="KIOSK", store_id=store_id)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    sale_resp = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, None, credit="300"),
        headers=_auth(token),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale_id = sale_resp.json()["id"]
    svc = SigningService(db_session)
    task = await svc.create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.TRANSACTION_ACK,
            contact_id=member_id,
            content={},
            ref_type="sale",
            ref_id=sale_id,
        ),
        created_by=clerk_id,
    )
    await db_session.commit()
    # 推送後作廢該單
    sales = SalesService(db_session)
    sale = await sales.get_sale(store_id, sale_id)
    assert sale is not None
    await sales.void_sale(sale, actor_user_id=clerk_id)
    await db_session.commit()
    # 客人此刻才透過**手持端 API** 簽 → 409，且任務作廢已提交（非 rollback 遺失）
    resp = await client.post(
        f"/api/v1/kiosk/tasks/{task.id}/sign",
        json={"signature_image_base64": _PNG},
        headers={"Authorization": f"Bearer {kiosk_token}"},
    )
    assert resp.status_code == 409, resp.text
    await db_session.refresh(task)
    assert task.status is SignatureTaskStatus.CANCELLED
    # 手持端輪詢不再看到此任務
    cur = await client.get(
        "/api/v1/kiosk/tasks/current", headers={"Authorization": f"Bearer {kiosk_token}"}
    )
    assert cur.status_code == 200 and cur.json() is None, cur.text


async def test_ack_push_rejected_after_partial_return(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已有退貨列（含部分退貨）的銷售不可推送簽收 → 422（原總額已非交易實態）。"""
    import pytest

    from app.modules.returns.models import CustomerReturn
    from app.shared.exceptions import SignatureContentMismatch

    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    sale_resp = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, None, credit="300"),
        headers=_auth(token),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale_id = sale_resp.json()["id"]
    # 部分退貨（僅退 100；sale.status 仍 COMPLETED、非 RETURNED）
    db_session.add(
        CustomerReturn(
            store_id=store_id,
            sale_id=sale_id,
            refund_amount=Decimal("100"),
            reason="部分退貨",
            clerk_user_id=clerk_id,
        )
    )
    await db_session.flush()
    with pytest.raises(SignatureContentMismatch):
        await SigningService(db_session).create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.TRANSACTION_ACK,
                contact_id=member_id,
                content={},
                ref_type="sale",
                ref_id=sale_id,
            ),
            created_by=clerk_id,
        )


async def test_signed_balance_snapshot_must_match_at_checkout(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """簽署後帳本變動（他筆入帳）→ 結帳 422：客人簽的「目前餘額/折抵後剩餘」不得漂移
    （Codex K5 第六輪 high）。餘額未變的相同流程則照常成立（既有 happy-path 覆蓋）。"""
    from sqlalchemy import text as sql_text

    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    # 簽署當下餘額 1000（快照 balance_before=1000, after=700）
    task_id = await _signed_use_task(db_session, store_id, member_id, clerk_id, debit="300")

    # 簽署後另一筆收購再入帳 +500 → 餘額 1500，簽的快照已過期
    acq_id = await db_session.scalar(
        sql_text(
            "INSERT INTO acquisitions"
            " (store_id, type, contact_id, clerk_user_id, total_cash_paid,"
            "  payout_method, payout_cash_amount, payout_credit_cash_equivalent,"
            "  created_at, updated_at)"
            " VALUES (:sid, 'BUYOUT', :cid, :uid, 0, 'STORE_CREDIT', 0, 500, now(), now())"
            " RETURNING id"
        ),
        {"sid": store_id, "cid": member_id, "uid": clerk_id},
    )
    await db_session.execute(
        sql_text(
            "INSERT INTO serialized_items"
            " (store_id, item_code, name, grade, ownership_type, acquisition_cost,"
            "  listed_price, acquisition_id, created_at, updated_at)"
            " VALUES (:sid, :code, '追加品', 'A', 'OWNED', 500, 500, :aid, now(), now())"
        ),
        {"sid": store_id, "code": f"K5-DRIFT-{member_id}", "aid": acq_id},
    )
    await StoreCreditService(db_session).credit(
        store_id,
        member_id,
        cash_equivalent=Decimal("500"),
        premium_rate=Decimal("0"),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=acq_id,
        created_by=clerk_id,
    )
    await db_session.commit()

    resp = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, task_id, credit="300"),
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text
    # 無副作用：餘額仍 1500
    balance = await StoreCreditService(db_session).get_balance(store_id, member_id)
    assert balance == Decimal("1500")


async def test_store_credit_use_content_is_canonical(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """扣抵確認內容整份 canonical（Codex K5 第七輪）：客端夾帶的其他鍵一律剝除、
    缺有效 debit/sale_total 即拒——客人簽的每個欄位都必須是結帳綁定或後端權威補齊的。"""
    import pytest

    from app.shared.exceptions import SignatureContentMismatch

    _token, store_id, clerk_id = await _seed(db_session)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    svc = SigningService(db_session)
    # 夾帶 items/note/假餘額 → 全數剝除，快照僅含 canonical 六鍵
    task = await svc.create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.STORE_CREDIT_USE,
            contact_id=member_id,
            content={
                "debit": "300",
                "sale_total": "300",
                "items": [{"name": "偽品項", "amount": "999"}],
                "note": "tampered",
                "balance_before": "99999",
            },
        ),
        created_by=clerk_id,
    )
    await db_session.flush()
    assert set(task.content.keys()) == {
        "seller_name",
        "phone",
        "debit",
        "sale_total",
        "balance_before",
        "balance_after",
    }
    assert task.content["balance_before"] == "1000"  # 後端權威、非客端假值
    # 缺 sale_total → 拒
    with pytest.raises(SignatureContentMismatch):
        await svc.create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.STORE_CREDIT_USE,
                contact_id=member_id,
                content={"debit": "300"},
            ),
            created_by=clerk_id,
        )


async def test_affidavit_extra_keys_stripped(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """收購切結客端鍵白名單（items/total/lot）：其他鍵不進快照（同第七輪原則）。"""
    from app.core.crypto import get_pii_cipher, national_id_blind_index

    _token, store_id, clerk_id = await _seed(db_session)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    member = await db_session.scalar(select(Contact).where(Contact.id == member_id))
    assert member is not None
    member.national_id_enc = get_pii_cipher().encrypt("A123456789")
    member.national_id_blind_index = national_id_blind_index("A123456789")
    await db_session.flush()
    task = await SigningService(db_session).create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
            contact_id=member_id,
            content={
                "items": [{"name": "相機", "amount": "1800"}],
                "total": "1800",
                "note": "unbound-extra",
                "fake_amount": "999999",
            },
        ),
        created_by=clerk_id,
    )
    await db_session.flush()
    assert "note" not in task.content and "fake_amount" not in task.content
    assert task.content["total"] == "1800"  # 白名單鍵保留（收購綁定精確比對）


async def test_affidavit_nested_extras_stripped(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """切結巢狀鍵深度 canonical（Codex K5 第八輪）：items[] 內多餘敘述（序號/成色/來源…）與
    lot 內多餘鍵全數剝除，快照僅存綁定會驗的最小形狀；非法形狀（字串 items）即拒。"""
    import pytest

    from app.core.crypto import get_pii_cipher, national_id_blind_index
    from app.shared.exceptions import SignatureContentMismatch

    _token, store_id, clerk_id = await _seed(db_session)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    member = await db_session.scalar(select(Contact).where(Contact.id == member_id))
    assert member is not None
    member.national_id_enc = get_pii_cipher().encrypt("A123456789")
    member.national_id_blind_index = national_id_blind_index("A123456789")
    await db_session.flush()
    svc = SigningService(db_session)
    task = await svc.create_task(
        store_id,
        SignatureTaskCreate(
            kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
            contact_id=member_id,
            content={
                "items": [
                    {
                        "name": "相機",
                        "amount": "1800",
                        "serial": "SN-FAKE-1",
                        "condition": "極新",
                        "origin": "自宅",
                    }
                ],
                "total": "1800",
                "lot": {"total_qty": 3, "acquisition_basis": "BAG", "note": "偽敘述"},
            },
        ),
        created_by=clerk_id,
    )
    await db_session.flush()
    items = task.content["items"]
    assert isinstance(items, list) and items[0] == {"name": "相機", "amount": "1800"}
    assert task.content["lot"] == {"total_qty": 3, "acquisition_basis": "BAG"}
    # 非法形狀：items 為字串 → 建立即拒（渲染不可能出現未綁定敘述）
    with pytest.raises(SignatureContentMismatch):
        await svc.create_task(
            store_id,
            SignatureTaskCreate(
                kind=SignatureTaskKind.ACQUISITION_AFFIDAVIT,
                contact_id=member_id,
                content={"items": "單一字串品項", "total": "100"},
            ),
            created_by=clerk_id,
        )


async def test_abandoned_signed_task_can_be_cancelled_then_unusable(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已簽未綁的扣抵授權可作廢（結帳被放棄時收回），作廢後不可再綁結帳（Codex K5 第十一輪）。
    已綁定銷售者不可作廢（交易證據）。"""
    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)
    task_id = await _signed_use_task(db_session, store_id, member_id, clerk_id, debit="300")

    # 已簽未綁 → 可作廢
    cancel = await client.post(
        f"/api/v1/signing/tasks/{task_id}/cancel", headers=_auth(token)
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "CANCELLED"
    # 作廢後不可綁結帳
    resp = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, task_id, credit="300"),
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text

    # 綁定後不可作廢（交易證據）
    task2 = await _signed_use_task(db_session, store_id, member_id, clerk_id, debit="300")
    ok_sale = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, task2, credit="300"),
        headers=_auth(token),
    )
    assert ok_sale.status_code == 201, ok_sale.text
    cancel2 = await client.post(
        f"/api/v1/signing/tasks/{task2}/cancel", headers=_auth(token)
    )
    assert cancel2.status_code == 409, cancel2.text


async def test_signed_task_binding_ttl_expires(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """逾綁定時效（15 分鐘）的已簽扣抵授權不可綁新銷售 → 422；已綁定銷售的回應遺失重放
    不受時效影響（Codex K5 第十一輪）。"""
    from sqlalchemy import text as sql_text

    token, store_id, clerk_id = await _seed(db_session)
    product_id = await _seed_catalog(db_session, store_id, price="300", qty=5)
    member_id = await _seed_member_with_credit(db_session, store_id, clerk_id, 1000)

    # 逾時未用 → 綁定 422
    stale = await _signed_use_task(db_session, store_id, member_id, clerk_id, debit="300")
    await db_session.execute(
        sql_text("UPDATE signature_tasks SET signed_at = now() - interval '16 minutes'"
                 " WHERE id = :tid"),
        {"tid": stale},
    )
    await db_session.commit()
    resp = await client.post(
        "/api/v1/sales",
        json=_sale_body(product_id, member_id, stale, credit="300"),
        headers=_auth(token),
    )
    assert resp.status_code == 422, resp.text

    # 時效內綁定成功 → 事後老化 → 同簽署新鍵重試仍回放原單（回放不走時效檢查）
    fresh = await _signed_use_task(db_session, store_id, member_id, clerk_id, debit="300")
    body = _sale_body(product_id, member_id, fresh, credit="300")
    first = await client.post("/api/v1/sales", json=body, headers=_auth(token))
    assert first.status_code == 201, first.text
    sale_id = first.json()["id"]
    await db_session.execute(
        sql_text("UPDATE signature_tasks SET signed_at = now() - interval '2 hours'"
                 " WHERE id = :tid"),
        {"tid": fresh},
    )
    await db_session.commit()
    retry = await client.post("/api/v1/sales", json=body, headers=_auth(token))
    assert retry.status_code == 201, retry.text
    assert retry.json()["id"] == sale_id
