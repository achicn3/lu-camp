"""收購①：一次收購同時有買斷品與散裝（店主 2026-09-26 選「收購頁也要」）。

送出時拆成買斷一張、每堆散裝各一張（作廢、報表、憑證照單張規則），但客人只簽一次、只付一次錢；
簽署內容由後端依同一份資料產生，付款時精確比對。整個動作同一交易：任一張失敗全部不成立。
"""

import itertools
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.main import create_app
from app.modules.acquisition.models import Acquisition
from app.modules.cashdrawer.models import CashMovement
from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.settings.models import StoreSettings
from app.modules.signing.models import SignatureTask
from app.modules.signing.service import SigningService
from app.shared.enums import (
    BulkLotStatus,
    CashMovementType,
    PayoutMethod,
    SerializedItemStatus,
    SignatureTaskStatus,
)
from tests.integration.customer_display_helpers import (
    ensure_paired_customer_display,
    signature_png_base64,
)
from tests.integration.test_intake_payment import Ctx, _ctx

PATH = "/api/v1/acquisitions/combined"
_keys = itertools.count()


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


def _body(ctx: Ctx, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "contact_id": ctx.contact_id,
        "items": [
            {"name": "黑色折疊椅", "grade": "B", "listed_price": "500", "acquisition_cost": "250"},
            {"name": "黑色折疊椅", "grade": "B", "listed_price": "500", "acquisition_cost": "250"},
        ],
        "lots": [
            {
                "name": "營釘",
                "acquisition_cost": "50",
                "acquisition_basis": "UNSPECIFIED",
                "total_qty": 10,
                "unit_price": "20",
            }
        ],
        "payout_method": "CASH",
    }
    body.update(overrides)
    return body


async def _submit(
    client: httpx.AsyncClient, ctx: Ctx, body: dict[str, Any], key: str | None = None
) -> httpx.Response:
    return await client.post(
        PATH,
        json=body,
        headers={**ctx.auth, "Idempotency-Key": key or f"combo-{next(_keys)}"},
    )


async def _cash_out(db: AsyncSession, ids: list[int]) -> Decimal:
    total = await db.scalar(
        select(func.coalesce(func.sum(CashMovement.amount), 0)).where(
            CashMovement.type == CashMovementType.BUYOUT_OUT, CashMovement.ref_id.in_(ids)
        )
    )
    return Decimal(total or 0)


async def test_one_submit_creates_a_buyout_and_a_bulk_acquisition(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    resp = await _submit(client, ctx, _body(ctx))
    assert resp.status_code == 201, resp.text
    results = resp.json()["results"]
    assert [r["type"] for r in results] == ["BUYOUT", "BULK_LOT"]
    assert len(results[0]["item_codes"]) == 2 and results[1]["lot_code"]
    ids = [r["acquisition_id"] for r in results]
    items = (
        await db_session.scalars(
            select(SerializedItem).where(SerializedItem.acquisition_id == ids[0])
        )
    ).all()
    assert [i.status for i in items] == [SerializedItemStatus.IN_STOCK] * 2
    lot = await db_session.scalar(select(BulkLot).where(BulkLot.acquisition_id == ids[1]))
    assert lot is not None and lot.status is BulkLotStatus.ON_SALE
    assert await _cash_out(db_session, ids) == Decimal(550)


async def test_retry_with_the_same_key_does_not_pay_twice(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    first = await _submit(client, ctx, _body(ctx), key="combo-same")
    again = await _submit(client, ctx, _body(ctx), key="combo-same")
    assert first.status_code == again.status_code == 201
    ids = [r["acquisition_id"] for r in first.json()["results"]]
    assert [r["acquisition_id"] for r in again.json()["results"]] == ids
    assert await _cash_out(db_session, ids) == Decimal(550)


async def test_split_payout_is_not_offered(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    resp = await _submit(client, ctx, _body(ctx, payout_method="SPLIT"))
    assert resp.status_code == 422, resp.text


async def test_nothing_is_created_when_the_drawer_is_closed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client, open_drawer=False)
    resp = await _submit(client, ctx, _body(ctx))
    assert resp.status_code == 409 and "開帳" in resp.text
    count = await db_session.scalar(
        select(func.count())
        .select_from(Acquisition)
        .where(Acquisition.contact_id == ctx.contact_id)
    )
    assert count == 0


async def test_required_signature_is_enforced(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    db_session.add(StoreSettings(store_id=ctx.store_id, require_acquisition_affidavit=True))
    await db_session.flush()
    resp = await _submit(client, ctx, _body(ctx))
    assert resp.status_code == 422 and "切結" in resp.text


async def _sign(
    db: AsyncSession, client: httpx.AsyncClient, ctx: Ctx, body: dict[str, Any]
) -> dict[str, Any]:
    terminal, device = await ensure_paired_customer_display(
        db, store_id=ctx.store_id, actor_user_id=ctx.clerk_id
    )
    request = {k: v for k, v in body.items() if k != "payout_method"}
    resp = await client.post(
        f"{PATH}/affidavit", json={**request, "terminal_id": terminal.id}, headers=ctx.auth
    )
    assert resp.status_code == 201, resp.text
    task: dict[str, Any] = resp.json()
    signing = SigningService(db)
    await signing.acknowledge_task(ctx.store_id, device.id, task["id"])
    await signing.sign_task(
        ctx.store_id,
        task["id"],
        device_id=device.id,
        signature_image_base64=signature_png_base64(),
        chosen_payout=PayoutMethod.STORE_CREDIT,
    )
    return task


async def test_signed_combined_acquisition_uses_one_signature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    body = _body(ctx, payout_method="STORE_CREDIT")
    task = await _sign(db_session, client, ctx, body)
    assert task["content"]["items"] == [
        {"name": "黑色折疊椅", "amount": "250"},
        {"name": "黑色折疊椅", "amount": "250"},
        {"name": "營釘 ×10", "amount": "50"},
    ]
    assert task["content"]["total"] == "550"
    resp = await _submit(client, ctx, {**body, "signature_task_id": task["id"]})
    assert resp.status_code == 201, resp.text
    assert {r["payout_method"] for r in resp.json()["results"]} == {"STORE_CREDIT"}
    stored = await db_session.get(SignatureTask, task["id"])
    assert stored is not None
    await db_session.refresh(stored)
    assert stored.status is SignatureTaskStatus.CONSUMED


async def test_changing_anything_after_signing_needs_a_new_signature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    body = _body(ctx, payout_method="STORE_CREDIT")
    task = await _sign(db_session, client, ctx, body)
    changed = {**body, "signature_task_id": task["id"]}
    changed["lots"] = [{**body["lots"][0], "total_qty": 12}]
    resp = await _submit(client, ctx, changed)
    assert resp.status_code == 422 and "重新簽署" in resp.text


async def test_payout_must_match_what_the_customer_chose(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    body = _body(ctx, payout_method="STORE_CREDIT")
    task = await _sign(db_session, client, ctx, body)
    resp = await _submit(
        client, ctx, {**body, "payout_method": "CASH", "signature_task_id": task["id"]}
    )
    assert resp.status_code == 422, resp.text
