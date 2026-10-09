"""排隊收購：待整理時客人不賣了（店主 2026-10-09）。

付款後、上架前客人改變主意要拿回去：在待整理頁直接退回——沿用收購「選品作廢」
（現金收回進抽屜／購物金沖回、那件退場、稽核），不是報廢（報廢會把成本算成損失）。
只限買斷的二手商品、限管理者；散裝照舊到收購紀錄整張作廢（寄售品不經待整理，不會出現在這裡）。
"""

from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.acquisition.models import AcquisitionVoid
from app.modules.cashdrawer.models import CashMovement
from app.modules.intake.models import IntakeBatch
from app.modules.inventory.models import SerializedItem
from app.modules.user.models import User
from app.shared.enums import (
    CashMovementType,
    IntakeBatchStatus,
    SerializedItemStatus,
    UserRole,
)
from tests.integration.test_intake_listing import BULK, _category, _items, _listing
from tests.integration.test_intake_payment import PATH, Ctx, _confirmed_batch, _ctx, _line, _pay


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


async def _paid(client: httpx.AsyncClient, ctx: Ctx) -> dict[str, Any]:
    """黑色折疊椅 ×2（買斷，每件 $250）＋營釘 10 件，付現。"""
    batch = await _confirmed_batch(client, ctx, [_line(qty=2), BULK], [2, 10])
    resp = await _pay(client, ctx, batch["id"])
    assert resp.status_code == 200, resp.text
    paid: dict[str, Any] = resp.json()
    return paid


async def _return(
    client: httpx.AsyncClient,
    ctx: Ctx,
    batch_id: int,
    item_id: int,
    *,
    kind: str = "SERIALIZED",
    auth: dict[str, str] | None = None,
) -> httpx.Response:
    return await client.post(
        f"{PATH}/{batch_id}/return-to-customer",
        json={"kind": kind, "id": item_id, "reason": "客人臨時不賣"},
        headers=auth or ctx.auth,
    )


async def test_returning_a_buyout_item_refunds_cash_and_removes_it(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid(client, ctx)
    chair = next(i for i in await _items(client, ctx, batch["id"]) if i["kind"] == "SERIALIZED")

    resp = await _return(client, ctx, batch["id"], chair["id"])

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"item_id": chair["id"], "reversed_cash": "250", "reversed_credit": "0"}
    item = await db_session.get(SerializedItem, chair["id"])
    assert item is not None
    await db_session.refresh(item)
    assert item.status is SerializedItemStatus.WRITTEN_OFF
    remaining = [
        i["id"] for i in await _items(client, ctx, batch["id"]) if i["kind"] == "SERIALIZED"
    ]
    assert chair["id"] not in remaining
    cash_in = await db_session.scalar(
        select(CashMovement).where(CashMovement.type == CashMovementType.ACQUISITION_VOID_IN)
    )
    assert cash_in is not None and cash_in.amount == 250
    void = await db_session.scalar(select(AcquisitionVoid))
    assert void is not None and void.item_ids == [chair["id"]]
    assert void.reason == "客人臨時不賣"


async def test_only_managers_can_return(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid(client, ctx)
    chair = next(i for i in await _items(client, ctx, batch["id"]) if i["kind"] == "SERIALIZED")
    clerk = User(store_id=ctx.store_id, username=f"c{ctx.store_id}", password_hash="h")
    clerk.role = UserRole.CLERK
    db_session.add(clerk)
    await db_session.flush()
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=ctx.store_id)

    resp = await _return(
        client, ctx, batch["id"], chair["id"], auth={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 403, resp.text


async def test_bulk_goes_to_acquisition_records(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid(client, ctx)
    pegs = next(i for i in await _items(client, ctx, batch["id"]) if i["kind"] == "BULK_LOT")

    resp = await _return(client, ctx, batch["id"], pegs["id"], kind="BULK_LOT")

    assert resp.status_code == 422, resp.text
    assert "收購紀錄" in resp.json()["detail"]


async def test_cash_refund_needs_an_open_drawer(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid(client, ctx)
    chair = next(i for i in await _items(client, ctx, batch["id"]) if i["kind"] == "SERIALIZED")
    current = (await client.get("/api/v1/cash-sessions/current", headers=ctx.auth)).json()
    closed = await client.post(
        f"/api/v1/cash-sessions/{current['id']}/close",
        json={"counted_amount": "0"},
        headers={**ctx.auth, "Idempotency-Key": f"close-{batch['id']}"},
    )
    assert closed.status_code in (200, 201), closed.text

    resp = await _return(client, ctx, batch["id"], chair["id"])

    assert resp.status_code == 409, resp.text


async def test_listed_items_are_not_returned_here(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid(client, ctx)
    category = await _category(db_session, ctx)
    chair = next(i for i in await _items(client, ctx, batch["id"]) if i["kind"] == "SERIALIZED")
    listed = await _listing(
        client,
        ctx,
        batch["id"],
        [{"kind": "SERIALIZED", "id": chair["id"], "category_id": category}],
        publish=True,
    )
    assert listed.status_code == 200, listed.text

    resp = await _return(client, ctx, batch["id"], chair["id"])

    assert resp.status_code == 409, resp.text


async def test_returning_the_last_pending_item_finishes_the_batch(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _confirmed_batch(client, ctx, [_line(qty=2)], [2])
    assert (await _pay(client, ctx, batch["id"])).status_code == 200
    category = await _category(db_session, ctx)
    first, second = await _items(client, ctx, batch["id"])
    listed = await _listing(
        client,
        ctx,
        batch["id"],
        [{"kind": "SERIALIZED", "id": first["id"], "category_id": category}],
        publish=True,
    )
    assert listed.status_code == 200, listed.text

    resp = await _return(client, ctx, batch["id"], second["id"])

    assert resp.status_code == 200, resp.text
    row = await db_session.get(IntakeBatch, batch["id"])
    assert row is not None
    await db_session.refresh(row)
    assert row.status is IntakeBatchStatus.LISTED


async def test_item_from_another_batch_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    one = await _paid(client, ctx)
    other = await _paid(client, ctx)
    chair = next(i for i in await _items(client, ctx, other["id"]) if i["kind"] == "SERIALIZED")

    resp = await _return(client, ctx, one["id"], chair["id"])

    assert resp.status_code == 409, resp.text
