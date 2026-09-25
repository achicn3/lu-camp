"""排隊收購：上架時的差異紀錄（docs/42 §8）。

整理時發現少件或壞到不能賣：記差異（件數、原因），那幾件出庫（報廢）。
成交件數與成本不改——客人簽過的就是簽過的；少掉的成本就是損失，散裝每件成本照原本算。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.main import create_app
from app.modules.inventory.models import BulkLot, SerializedItem, StockMovement
from app.shared.enums import BulkLotStatus, SerializedItemStatus, StockReason
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
    batch = await _confirmed_batch(client, ctx, [_line(qty=2), BULK], [2, 10])
    paid: dict[str, Any] = (await _pay(client, ctx, batch["id"])).json()
    return paid


async def _report(
    client: httpx.AsyncClient, ctx: Ctx, batch_id: int, body: dict[str, Any]
) -> httpx.Response:
    return await client.post(f"{PATH}/{batch_id}/discrepancies", json=body, headers=ctx.auth)


async def test_missing_piece_leaves_inventory_and_is_recorded(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid(client, ctx)
    chair = (await _items(client, ctx, batch["id"]))[0]
    resp = await _report(
        client,
        ctx,
        batch["id"],
        {"kind": "SERIALIZED", "id": chair["id"], "qty": 1, "reason": "找不到"},
    )
    assert resp.status_code == 201, resp.text
    item = await db_session.get(SerializedItem, chair["id"])
    assert item is not None
    await db_session.refresh(item)
    assert item.status is SerializedItemStatus.WRITTEN_OFF
    assert item.acquisition_cost == 250  # 成本不動
    movement = await db_session.scalar(
        select(StockMovement)
        .where(StockMovement.serialized_item_id == chair["id"])
        .where(StockMovement.reason == StockReason.WRITE_OFF)
    )
    assert movement is not None and movement.qty == 1
    remaining = await _items(client, ctx, batch["id"])
    serialized = [i["id"] for i in remaining if i["kind"] == "SERIALIZED"]
    assert chair["id"] not in serialized and len(serialized) == 1
    records = (await client.get(f"{PATH}/{batch['id']}/discrepancies", headers=ctx.auth)).json()
    assert [(r["name"], r["qty"], r["reason"]) for r in records] == [("黑色折疊椅", 1, "找不到")]


async def test_bulk_shortage_reduces_pieces_but_keeps_cost_per_piece(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid(client, ctx)
    pegs = (await _items(client, ctx, batch["id"]))[2]
    resp = await _report(
        client, ctx, batch["id"], {"kind": "BULK_LOT", "id": pegs["id"], "qty": 3, "reason": "少了"}
    )
    assert resp.status_code == 201, resp.text
    lot = await db_session.get(BulkLot, pegs["id"])
    assert lot is not None
    await db_session.refresh(lot)
    assert (lot.total_qty, lot.remaining_qty) == (10, 7)
    assert lot.acquisition_cost == Decimal(50)
    assert lot.status is BulkLotStatus.PENDING_LISTING
    after = (await _items(client, ctx, batch["id"]))[2]
    assert after["qty"] == 7 and after["acquisition_cost"] == "5"
    overview = (await client.get(f"{PATH}/awaiting-listing", headers=ctx.auth)).json()
    row = next(r for r in overview if r["id"] == batch["id"])
    assert row["pending_count"] == 2 + 7


async def test_shortage_cannot_exceed_what_is_there(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid(client, ctx)
    chair, _, pegs = await _items(client, ctx, batch["id"])
    too_many = await _report(
        client, ctx, batch["id"], {"kind": "BULK_LOT", "id": pegs["id"], "qty": 11, "reason": "x"}
    )
    assert too_many.status_code == 422, too_many.text
    two_chairs = await _report(
        client, ctx, batch["id"], {"kind": "SERIALIZED", "id": chair["id"], "qty": 2, "reason": "x"}
    )
    assert two_chairs.status_code == 422, two_chairs.text


async def test_whole_lot_short_writes_it_off(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid(client, ctx)
    pegs = (await _items(client, ctx, batch["id"]))[2]
    await _report(
        client,
        ctx,
        batch["id"],
        {"kind": "BULK_LOT", "id": pegs["id"], "qty": 10, "reason": "整包不見"},
    )
    lot = await db_session.get(BulkLot, pegs["id"])
    assert lot is not None
    await db_session.refresh(lot)
    assert lot.status is BulkLotStatus.WRITTEN_OFF and lot.remaining_qty == 0


async def test_listed_items_cannot_get_a_discrepancy(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid(client, ctx)
    category_id = await _category(db_session, ctx)
    chair = (await _items(client, ctx, batch["id"]))[0]
    await _listing(
        client,
        ctx,
        batch["id"],
        [{"kind": "SERIALIZED", "id": chair["id"], "category_id": category_id}],
        publish=True,
    )
    resp = await _report(
        client, ctx, batch["id"], {"kind": "SERIALIZED", "id": chair["id"], "qty": 1, "reason": "x"}
    )
    assert resp.status_code == 409 and "待整理" in resp.text


async def test_last_pending_item_short_completes_the_batch(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid(client, ctx)
    category_id = await _category(db_session, ctx)
    first, second, pegs = await _items(client, ctx, batch["id"])
    await _listing(
        client,
        ctx,
        batch["id"],
        [
            {"kind": "SERIALIZED", "id": first["id"], "category_id": category_id},
            {"kind": "BULK_LOT", "id": pegs["id"], "category_id": category_id},
        ],
        publish=True,
    )
    await _report(
        client,
        ctx,
        batch["id"],
        {"kind": "SERIALIZED", "id": second["id"], "qty": 1, "reason": "壞了"},
    )
    got = (await client.get(f"{PATH}/{batch['id']}", headers=ctx.auth)).json()
    assert got["status"] == "LISTED"
