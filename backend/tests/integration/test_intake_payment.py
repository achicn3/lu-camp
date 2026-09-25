"""排隊收購 I3：簽署與付款（docs/42 §6；2026-09-25 店主選「付款當下建庫存、標待整理」）。

付款時沿用現有收購（撥款、錢櫃、購物金、作廢全同一套）：依類型各成立一筆收購（買斷／寄售／散裝），
商品一起建好但標「待整理」——POS 賣不到、不算在架上；空檔再補資料上架（I4）。
一批一份簽署：切結內容由後端依接受的列產生，付款時比對，改過就要重簽。
"""

import itertools
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_pii_cipher, national_id_blind_index
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.acquisition.models import Acquisition
from app.modules.cashdrawer.models import CashMovement
from app.modules.contacts.models import Contact
from app.modules.intake.service import IntakeService
from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.settings.models import StoreSettings
from app.modules.signing.models import SignatureTask
from app.modules.signing.service import SigningService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    BulkLotStatus,
    CashMovementType,
    PayoutMethod,
    SerializedItemStatus,
    SignatureTaskStatus,
    UserRole,
)
from tests.integration.customer_display_helpers import (
    ensure_paired_customer_display,
    signature_png_base64,
)

PATH = "/api/v1/intake-batches"
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


class Ctx:
    store_id: int
    clerk_id: int
    contact_id: int
    auth: dict[str, str]


async def _ctx(db: AsyncSession, client: httpx.AsyncClient, *, open_drawer: bool = True) -> Ctx:
    store = Store(name="門市")
    db.add(store)
    await db.flush()
    clerk = User(
        store_id=store.id, username=f"clk{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    contact = Contact(
        store_id=store.id,
        name="王小明",
        phone="0912345678",
        national_id_enc=get_pii_cipher().encrypt("A123456789"),
        national_id_blind_index=national_id_blind_index("A123456789"),
        roles=["SELLER", "MEMBER"],
    )
    db.add_all([clerk, contact])
    await db.flush()
    ctx = Ctx()
    ctx.store_id, ctx.clerk_id, ctx.contact_id = store.id, clerk.id, contact.id
    token = encode_access_token(user_id=clerk.id, role="MANAGER", store_id=store.id)
    ctx.auth = {"Authorization": f"Bearer {token}"}
    if open_drawer:
        resp = await client.post(
            "/api/v1/cash-sessions/open",
            json={"opening_float": "5000"},
            headers={**ctx.auth, "Idempotency-Key": f"open-{next(_idem)}"},
        )
        assert resp.status_code in (200, 201), resp.text
    return ctx


def _line(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "short_name": "黑色折疊椅",
        "qty": 2,
        "acquisition_type": "BUYOUT",
        "reference_price": "1000",
        "discount_pct": 50,
        "expected_listed_price": "500",
        "suggested_cost": "256",
        "deal_cost": "250",
        "grade": "A",
    }
    base.update(overrides)
    return base


async def _confirmed_batch(
    client: httpx.AsyncClient, ctx: Ctx, lines: list[dict[str, object]], accept: list[int | None]
) -> dict[str, Any]:
    """建批次、加列、送叫號，逐列接受（None＝客人不售）。"""
    batch = (
        await client.post(
            PATH, json={"contact_id": ctx.contact_id, "declared_item_count": 1}, headers=ctx.auth
        )
    ).json()
    ids = []
    for line in lines:
        resp = await client.post(f"{PATH}/{batch['id']}/lines", json=line, headers=ctx.auth)
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["id"])
    assert (await client.post(f"{PATH}/{batch['id']}/ready", headers=ctx.auth)).status_code == 200
    for line_id, qty in zip(ids, accept, strict=True):
        body = (
            {"disposition": "CUSTOMER_KEPT", "accepted_qty": 0, "returned_to_customer": True}
            if qty is None
            else {"disposition": "ACCEPTED", "accepted_qty": qty}
        )
        resp = await client.patch(
            f"{PATH}/{batch['id']}/lines/{line_id}/disposition", json=body, headers=ctx.auth
        )
        assert resp.status_code == 200, resp.text
    got: dict[str, Any] = (await client.get(f"{PATH}/{batch['id']}", headers=ctx.auth)).json()
    return got


async def _pay(
    client: httpx.AsyncClient, ctx: Ctx, batch_id: int, payout: str = "CASH"
) -> httpx.Response:
    return await client.post(
        f"{PATH}/{batch_id}/pay", json={"payout_method": payout}, headers=ctx.auth
    )


# ── 估完要有預計售價（建庫存需要售價）────────────────────────────────


async def test_ready_requires_expected_listed_price(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = (
        await client.post(
            PATH, json={"contact_id": ctx.contact_id, "declared_item_count": 1}, headers=ctx.auth
        )
    ).json()
    await client.post(
        f"{PATH}/{batch['id']}/lines", json=_line(expected_listed_price=None), headers=ctx.auth
    )
    resp = await client.post(f"{PATH}/{batch['id']}/ready", headers=ctx.auth)
    assert resp.status_code == 409 and "預計售價" in resp.text


# ── 付款：成立收購、商品待整理 ──────────────────────────────────────


async def test_cash_payment_creates_acquisition_with_pending_items(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """3 張椅子收 2 張：一筆買斷收購、2 件待整理商品、錢櫃付出 500、批次已付款。"""
    ctx = await _ctx(db_session, client)
    batch = await _confirmed_batch(client, ctx, [_line(qty=3)], [2])
    resp = await _pay(client, ctx, batch["id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "PAID"
    assert len(body["acquisition_ids"]) == 1

    acquisition = await db_session.get(Acquisition, body["acquisition_ids"][0])
    assert acquisition is not None and acquisition.total_cash_paid == Decimal(500)
    items = (
        await db_session.scalars(
            select(SerializedItem).where(SerializedItem.acquisition_id == acquisition.id)
        )
    ).all()
    assert len(items) == 2
    assert {i.status for i in items} == {SerializedItemStatus.PENDING_LISTING}
    assert {(i.name, i.listed_price, i.acquisition_cost) for i in items} == {
        ("黑色折疊椅", Decimal(500), Decimal(250))
    }
    paid_out = await db_session.scalar(
        select(func.sum(CashMovement.amount)).where(
            CashMovement.type == CashMovementType.BUYOUT_OUT,
            CashMovement.ref_id == acquisition.id,
        )
    )
    assert paid_out == Decimal(500)


async def test_mixed_batch_creates_one_acquisition_per_type(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _confirmed_batch(
        client,
        ctx,
        [
            _line(qty=1),
            _line(
                short_name="帳篷",
                qty=1,
                acquisition_type="CONSIGNMENT",
                deal_cost=None,
                suggested_cost=None,
                commission_pct=40,
                expected_listed_price="6000",
            ),
            _line(
                short_name="營釘",
                qty=30,
                acquisition_type="BULK_LOT",
                deal_cost="5",
                expected_listed_price="20",
                grade=None,
            ),
        ],
        [1, 1, 30],
    )
    resp = await _pay(client, ctx, batch["id"])
    assert resp.status_code == 200, resp.text
    acquisitions = [
        await db_session.get(Acquisition, acq_id) for acq_id in resp.json()["acquisition_ids"]
    ]
    assert sorted(a.type.value for a in acquisitions if a is not None) == [
        "BULK_LOT",
        "BUYOUT",
        "CONSIGNMENT",
    ]
    lot = await db_session.scalar(select(BulkLot).where(BulkLot.name == "營釘"))
    assert lot is not None
    assert (lot.status, lot.total_qty, lot.acquisition_cost, lot.unit_price) == (
        BulkLotStatus.PENDING_LISTING,
        30,
        Decimal(150),
        Decimal(20),
    )
    consigned = await db_session.scalar(select(SerializedItem).where(SerializedItem.name == "帳篷"))
    assert consigned is not None
    assert (consigned.status, consigned.commission_pct) == (
        SerializedItemStatus.PENDING_LISTING,
        40,
    )


async def test_payment_is_idempotent(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """付款按兩次（或回應遺失重送）：只付一次錢、只建一次收購。"""
    ctx = await _ctx(db_session, client)
    batch = await _confirmed_batch(client, ctx, [_line(qty=1)], [1])
    first = await _pay(client, ctx, batch["id"])
    again = await _pay(client, ctx, batch["id"])
    assert first.status_code == again.status_code == 200
    assert first.json()["acquisition_ids"] == again.json()["acquisition_ids"]
    count = await db_session.scalar(
        select(func.count()).select_from(Acquisition).where(Acquisition.store_id == ctx.store_id)
    )
    assert count == 1


async def test_nothing_accepted_cannot_be_paid(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _confirmed_batch(client, ctx, [_line(qty=1)], [None])
    resp = await _pay(client, ctx, batch["id"])
    assert resp.status_code == 409, resp.text


async def test_undecided_lines_block_signing_and_payment(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _confirmed_batch(
        client, ctx, [_line(qty=1), _line(short_name="帳篷", qty=1)], [1, 1]
    )
    second = batch["lines"][1]["id"]
    resp = await client.patch(
        f"{PATH}/{batch['id']}/lines/{second}/disposition",
        json={"disposition": "PENDING", "accepted_qty": 0},
        headers=ctx.auth,
    )
    assert resp.status_code == 200, resp.text
    resp = await _pay(client, ctx, batch["id"])
    assert resp.status_code == 409 and "第 2 列" in resp.text
    resp = await client.post(
        f"{PATH}/{batch['id']}/signature", json={"terminal_id": None}, headers=ctx.auth
    )
    assert resp.status_code == 409 and "第 2 列" in resp.text


async def test_cash_payment_needs_an_open_drawer(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client, open_drawer=False)
    batch = await _confirmed_batch(client, ctx, [_line(qty=1)], [1])
    resp = await _pay(client, ctx, batch["id"])
    assert resp.status_code == 409 and "開帳" in resp.text
    got = (await client.get(f"{PATH}/{batch['id']}", headers=ctx.auth)).json()
    assert got["status"] == "AWAITING_CONFIRM"  # 沒付成就不動


async def test_signature_required_by_settings(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    db_session.add(StoreSettings(store_id=ctx.store_id, require_acquisition_affidavit=True))
    await db_session.flush()
    batch = await _confirmed_batch(client, ctx, [_line(qty=1)], [1])
    resp = await _pay(client, ctx, batch["id"])
    assert resp.status_code == 409 and "簽署" in resp.text


# ── 簽署：內容由後端依接受的列產生；付款時比對；單次使用 ──────────────


async def _sign(db: AsyncSession, ctx: Ctx, client: httpx.AsyncClient, batch_id: int) -> int:
    terminal, device = await ensure_paired_customer_display(
        db, store_id=ctx.store_id, actor_user_id=ctx.clerk_id
    )
    resp = await client.post(
        f"{PATH}/{batch_id}/signature", json={"terminal_id": terminal.id}, headers=ctx.auth
    )
    assert resp.status_code == 201, resp.text
    task_id = int(resp.json()["signature_task_id"])
    signing = SigningService(db)
    await signing.acknowledge_task(ctx.store_id, device.id, task_id)
    await signing.sign_task(
        ctx.store_id,
        task_id,
        device_id=device.id,
        signature_image_base64=signature_png_base64(),
        chosen_payout=PayoutMethod.STORE_CREDIT,
    )
    return task_id


async def test_pay_waits_while_the_customer_is_signing_and_ignores_a_withdrawn_signature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _confirmed_batch(client, ctx, [_line(qty=1)], [1])
    terminal, _device = await ensure_paired_customer_display(
        db_session, store_id=ctx.store_id, actor_user_id=ctx.clerk_id
    )
    resp = await client.post(
        f"{PATH}/{batch['id']}/signature", json={"terminal_id": terminal.id}, headers=ctx.auth
    )
    task_id = int(resp.json()["signature_task_id"])
    resp = await _pay(client, ctx, batch["id"])
    assert resp.status_code == 409 and "簽名" in resp.text

    # 撤回簽名後，本店沒規定一定要簽 → 可以不簽直接付
    await SigningService(db_session).cancel_task(ctx.store_id, task_id, actor_user_id=ctx.clerk_id)
    resp = await _pay(client, ctx, batch["id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "PAID"


async def test_signed_batch_pays_with_the_customers_choice_and_consumes_the_signature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _confirmed_batch(client, ctx, [_line(qty=2)], [2])
    task_id = await _sign(db_session, ctx, client, batch["id"])
    content = (await db_session.get(SignatureTask, task_id)).content  # type: ignore[union-attr]
    assert content["items"] == [
        {"name": "黑色折疊椅", "amount": "250"},
        {"name": "黑色折疊椅", "amount": "250"},
    ]
    assert content["total"] == "500"

    resp = await _pay(client, ctx, batch["id"], payout="CASH")  # 客人簽的是購物金，以客人為準
    assert resp.status_code == 200, resp.text
    acquisition = await db_session.get(Acquisition, resp.json()["acquisition_ids"][0])
    assert acquisition is not None and acquisition.payout_method is PayoutMethod.STORE_CREDIT
    task = await db_session.get(SignatureTask, task_id)
    assert task is not None and task.status is SignatureTaskStatus.CONSUMED


async def test_changes_after_signing_require_signing_again(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _confirmed_batch(client, ctx, [_line(qty=2)], [2])
    await _sign(db_session, ctx, client, batch["id"])
    line_id = batch["lines"][0]["id"]
    await client.patch(
        f"{PATH}/{batch['id']}/lines/{line_id}", json={"deal_cost": "300"}, headers=ctx.auth
    )
    resp = await _pay(client, ctx, batch["id"])
    assert resp.status_code == 409 and "重新簽署" in resp.text


# ── 收購明細（含簽名）：整批一張，內容＝客人簽的 ─────────────────────


async def test_receipt_prints_what_the_customer_signed_for_the_whole_batch(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    bulk = _line(
        short_name="營釘",
        qty=10,
        acquisition_type="BULK_LOT",
        deal_cost="5",
        expected_listed_price="20",
        grade=None,
    )
    batch = await _confirmed_batch(client, ctx, [_line(qty=2), bulk], [2, 10])
    task_id = await _sign(db_session, ctx, client, batch["id"])
    paid = (await _pay(client, ctx, batch["id"])).json()

    resp = await client.get(f"{PATH}/{batch['id']}/receipt", headers=ctx.auth)
    assert resp.status_code == 200, resp.text
    receipt = resp.json()
    assert receipt["items"] == [
        {"name": "黑色折疊椅", "amount": "250"},
        {"name": "黑色折疊椅", "amount": "250"},
        {"name": "營釘 ×10", "amount": "50"},
    ]
    assert receipt["total"] == "550"
    assert receipt["payout_method"] == "STORE_CREDIT"
    assert receipt["signature_task_id"] == task_id
    assert receipt["acquisition_id"] in paid["acquisition_ids"]
    assert paid["ticket_label"] in receipt["reference"]
    assert all(f"#{n}" in receipt["reference"] for n in paid["acquisition_ids"])
    contact = await db_session.get(Contact, ctx.contact_id)
    assert contact is not None
    # 兩筆收購各撥一次購物金：印的是加總，餘額是最後一筆撥入後的帳本餘額
    assert Decimal(receipt["store_credit_granted"]) >= Decimal(550)
    assert Decimal(receipt["store_credit_balance_after"]) == Decimal(
        receipt["store_credit_granted"]
    )


async def test_receipt_needs_payment_and_a_signature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _confirmed_batch(client, ctx, [_line(qty=1)], [1])
    resp = await client.get(f"{PATH}/{batch['id']}/receipt", headers=ctx.auth)
    assert resp.status_code == 409 and "付款" in resp.text
    await _pay(client, ctx, batch["id"])  # 本店沒規定要簽：不簽直接付現
    resp = await client.get(f"{PATH}/{batch['id']}/receipt", headers=ctx.auth)
    assert resp.status_code == 409 and "簽名" in resp.text


# ── 待整理商品不能賣；作廢收購一併取消 ──────────────────────────────


async def test_pending_items_cannot_be_sold(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _confirmed_batch(client, ctx, [_line(qty=1)], [1])
    acquisition_id = (await _pay(client, ctx, batch["id"])).json()["acquisition_ids"][0]
    item = await db_session.scalar(
        select(SerializedItem).where(SerializedItem.acquisition_id == acquisition_id)
    )
    assert item is not None
    resp = await client.post(
        "/api/v1/sales/quote",
        json={"lines": [{"line_type": "SERIALIZED", "item_code": item.item_code}]},
        headers=ctx.auth,
    )
    assert resp.status_code == 422 and "待整理" in resp.text


async def test_pending_bulk_lot_cannot_be_sold(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    line = _line(
        short_name="營釘",
        qty=10,
        acquisition_type="BULK_LOT",
        deal_cost="5",
        expected_listed_price="20",
        grade=None,
    )
    batch = await _confirmed_batch(client, ctx, [line], [10])
    acquisition_id = (await _pay(client, ctx, batch["id"])).json()["acquisition_ids"][0]
    lot = await db_session.scalar(select(BulkLot).where(BulkLot.acquisition_id == acquisition_id))
    assert lot is not None
    resp = await client.post(
        "/api/v1/sales/quote",
        json={"lines": [{"line_type": "BULK_LOT", "bulk_lot_id": lot.id, "qty": 1}]},
        headers=ctx.auth,
    )
    assert resp.status_code == 422 and "待整理" in resp.text


async def test_voiding_the_acquisition_writes_off_pending_items_and_returns_cash(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _confirmed_batch(client, ctx, [_line(qty=2)], [2])
    acquisition_id = (await _pay(client, ctx, batch["id"])).json()["acquisition_ids"][0]
    resp = await client.post(
        f"/api/v1/acquisitions/{acquisition_id}/void",
        json={"reason": "客人隔天反悔"},
        headers=ctx.auth,
    )
    assert resp.status_code == 200, resp.text
    statuses = (
        await db_session.scalars(
            select(SerializedItem.status).where(SerializedItem.acquisition_id == acquisition_id)
        )
    ).all()
    assert set(statuses) == {SerializedItemStatus.WRITTEN_OFF}
    refunded = await db_session.scalar(
        select(func.sum(CashMovement.amount)).where(
            CashMovement.type == CashMovementType.ACQUISITION_VOID_IN,
            CashMovement.ref_id == acquisition_id,
        )
    )
    assert refunded == Decimal(500)
    assert isinstance(IntakeService, type)
