"""排隊收購 I4：待整理上架（docs/42 §7、§8）。

付款時商品已建成「待整理」；上架＝補品名／成色／品牌型號／分類／售價，轉成可賣，印標籤。
成本與件數是客人簽過的，這裡不能改。可以分次上架；全部上完批次變「全部上架」。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.core.db import get_session
from app.main import create_app
from app.modules.inventory.models import BulkLot, Category, SerializedItem
from app.shared.enums import BulkLotStatus, SerializedItemStatus
from tests.integration.test_intake_payment import (
    PATH,
    Ctx,
    _confirmed_batch,
    _ctx,
    _line,
    _pay,
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


BULK = _line(
    short_name="營釘",
    qty=10,
    acquisition_type="BULK_LOT",
    deal_cost="5",
    expected_listed_price="20",
    grade=None,
)


async def _paid_batch(client: httpx.AsyncClient, ctx: Ctx) -> dict[str, Any]:
    batch = await _confirmed_batch(client, ctx, [_line(qty=2), BULK], [2, 10])
    resp = await _pay(client, ctx, batch["id"])
    assert resp.status_code == 200, resp.text
    paid: dict[str, Any] = resp.json()
    return paid


async def _category(db: AsyncSession, ctx: Ctx, name: str = "露營椅") -> int:
    category = Category(store_id=ctx.store_id, name=name, target_margin_pct=45)
    db.add(category)
    await db.flush()
    return category.id


async def _items(client: httpx.AsyncClient, ctx: Ctx, batch_id: int) -> list[dict[str, Any]]:
    resp = await client.get(f"{PATH}/{batch_id}/items", headers=ctx.auth)
    assert resp.status_code == 200, resp.text
    items: list[dict[str, Any]] = resp.json()
    return items


async def _listing(
    client: httpx.AsyncClient, ctx: Ctx, batch_id: int, items: list[dict[str, Any]], publish: bool
) -> httpx.Response:
    return await client.post(
        f"{PATH}/{batch_id}/listing",
        json={"items": items, "publish": publish},
        headers=ctx.auth,
    )


async def test_items_list_what_is_waiting_and_what_is_missing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid_batch(client, ctx)
    items = await _items(client, ctx, batch["id"])
    assert [(i["kind"], i["name"], i["qty"]) for i in items] == [
        ("SERIALIZED", "黑色折疊椅", 1),
        ("SERIALIZED", "黑色折疊椅", 1),
        ("BULK_LOT", "營釘", 10),
    ]
    chair, _, pegs = items
    assert chair["listed"] is False and chair["listed_price"] == "500"
    assert chair["acquisition_cost"] == "250" and pegs["acquisition_cost"] == "5"
    assert "分類" in chair["missing"] and "品牌" in chair["missing"]
    assert pegs["code"]


async def test_save_without_publishing_keeps_items_pending_and_audits(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid_batch(client, ctx)
    category_id = await _category(db_session, ctx)
    chair = (await _items(client, ctx, batch["id"]))[0]
    edit = {
        "kind": "SERIALIZED",
        "id": chair["id"],
        "name": "Coleman 折疊椅",
        "grade": "B",
        "category_id": category_id,
        "listed_price": "520",
        "note": "缺收納袋",
    }
    resp = await _listing(client, ctx, batch["id"], [edit], publish=False)
    assert resp.status_code == 200, resp.text
    item = await db_session.get(SerializedItem, chair["id"])
    assert item is not None
    await db_session.refresh(item)
    assert item.status is SerializedItemStatus.PENDING_LISTING
    assert (item.name, item.category_id) == ("Coleman 折疊椅", category_id)
    assert item.listed_price == Decimal(520)
    assert item.acquisition_cost == 250  # 成本不動
    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.entity_type == "serialized_item", AuditLog.entity_id == str(chair["id"])
        )
    )
    assert audit is not None and audit.after is not None and audit.after["listed_price"] == "520"


async def test_publish_part_then_all(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid_batch(client, ctx)
    category_id = await _category(db_session, ctx)
    first, second, pegs = await _items(client, ctx, batch["id"])

    resp = await _listing(
        client,
        ctx,
        batch["id"],
        [{"kind": "SERIALIZED", "id": first["id"], "category_id": category_id}],
        publish=True,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["batch_status"] == "PARTIALLY_LISTED"
    assert [i["code"] for i in body["listed"]] == [first["code"]]  # 前端拿去印標籤
    assert body["listed"][0]["category_name"] == "露營椅"
    item = await db_session.get(SerializedItem, first["id"])
    assert item is not None
    await db_session.refresh(item)
    assert item.status is SerializedItemStatus.IN_STOCK

    resp = await _listing(
        client,
        ctx,
        batch["id"],
        [
            {"kind": "SERIALIZED", "id": second["id"], "category_id": category_id},
            {
                "kind": "BULK_LOT",
                "id": pegs["id"],
                "category_id": category_id,
                "listed_price": "25",
            },
        ],
        publish=True,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["batch_status"] == "LISTED"
    lot = await db_session.get(BulkLot, pegs["id"])
    assert lot is not None
    await db_session.refresh(lot)
    assert lot.status is BulkLotStatus.ON_SALE and lot.unit_price == 25
    got = (await client.get(f"{PATH}/{batch['id']}", headers=ctx.auth)).json()
    assert got["status"] == "LISTED"


async def test_publish_needs_a_category(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid_batch(client, ctx)
    chair = (await _items(client, ctx, batch["id"]))[0]
    resp = await _listing(
        client, ctx, batch["id"], [{"kind": "SERIALIZED", "id": chair["id"]}], publish=True
    )
    assert resp.status_code == 422 and "分類" in resp.text


async def test_publishing_twice_does_not_fail_or_change_anything(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid_batch(client, ctx)
    category_id = await _category(db_session, ctx)
    chair = (await _items(client, ctx, batch["id"]))[0]
    edit = {"kind": "SERIALIZED", "id": chair["id"], "category_id": category_id}
    assert (await _listing(client, ctx, batch["id"], [edit], publish=True)).status_code == 200
    again = await _listing(
        client, ctx, batch["id"], [{**edit, "listed_price": "999"}], publish=True
    )
    assert again.status_code == 200, again.text
    assert again.json()["listed"] == []  # 已上架的不再動、也不重印
    item = await db_session.get(SerializedItem, chair["id"])
    assert item is not None
    await db_session.refresh(item)
    assert item.listed_price == 500


async def test_items_from_another_batch_are_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid_batch(client, ctx)
    other = await _paid_batch(client, ctx)
    category_id = await _category(db_session, ctx)
    stranger = (await _items(client, ctx, other["id"]))[0]
    resp = await _listing(
        client,
        ctx,
        batch["id"],
        [{"kind": "SERIALIZED", "id": stranger["id"], "category_id": category_id}],
        publish=True,
    )
    assert resp.status_code == 409 and "不是這一批" in resp.text


async def test_unpaid_batch_has_nothing_to_list(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _confirmed_batch(client, ctx, [_line(qty=1)], [1])
    resp = await client.get(f"{PATH}/{batch['id']}/items", headers=ctx.auth)
    assert resp.status_code == 409 and "付款" in resp.text


async def test_awaiting_listing_overview_counts_pieces_oldest_first(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    first = await _paid_batch(client, ctx)
    second = await _paid_batch(client, ctx)
    category_id = await _category(db_session, ctx)
    chair = (await _items(client, ctx, first["id"]))[0]
    await _listing(
        client,
        ctx,
        first["id"],
        [{"kind": "SERIALIZED", "id": chair["id"], "category_id": category_id}],
        publish=True,
    )
    resp = await client.get(f"{PATH}/awaiting-listing", headers=ctx.auth)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["id"] for r in rows] == [first["id"], second["id"]]
    assert (rows[0]["listed_count"], rows[0]["pending_count"]) == (1, 11)
    assert (rows[1]["listed_count"], rows[1]["pending_count"]) == (0, 12)
    assert rows[0]["days_waiting"] == 0 and rows[0]["paid_at"] is not None


async def test_listed_items_can_be_sold(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _paid_batch(client, ctx)
    category_id = await _category(db_session, ctx)
    chair = (await _items(client, ctx, batch["id"]))[0]
    await _listing(
        client,
        ctx,
        batch["id"],
        [{"kind": "SERIALIZED", "id": chair["id"], "category_id": category_id}],
        publish=True,
    )
    resp = await client.post(
        "/api/v1/sales/quote",
        json={"lines": [{"line_type": "SERIALIZED", "item_code": chair["code"]}]},
        headers=ctx.auth,
    )
    assert resp.status_code == 200, resp.text


async def test_partly_listed_acquisition_cannot_be_voided(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """店主 2026-09-25：已經上架一部分就不能整張作廢（標籤已貼、商品已在架上）。"""
    ctx = await _ctx(db_session, client)
    batch = await _paid_batch(client, ctx)
    category_id = await _category(db_session, ctx)
    chair = (await _items(client, ctx, batch["id"]))[0]
    await _listing(
        client,
        ctx,
        batch["id"],
        [{"kind": "SERIALIZED", "id": chair["id"], "category_id": category_id}],
        publish=True,
    )
    buyout_id = min(batch["acquisition_ids"])
    rows = (await client.get("/api/v1/acquisitions", headers=ctx.auth)).json()["items"]
    row = next(r for r in rows if r["id"] == buyout_id)
    assert row["void_block"] == "PARTIALLY_LISTED"
    resp = await client.post(
        f"/api/v1/acquisitions/{buyout_id}/void", json={"reason": "反悔"}, headers=ctx.auth
    )
    assert resp.status_code == 409 and "上架" in resp.text


async def test_items_know_which_estimate_line_they_came_from(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同一列估的多件是同款：畫面據此合成一張卡，品牌型號只填一次（店主 2026-09-26）。"""
    ctx = await _ctx(db_session, client)
    tent = _line(short_name="帳篷", qty=1, deal_cost="800", expected_listed_price="1600")
    batch = await _confirmed_batch(client, ctx, [_line(qty=2), BULK, tent], [2, 10, 1])
    await _pay(client, ctx, batch["id"])
    items = await _items(client, ctx, batch["id"])
    assert [(i["name"], i["line_no"]) for i in items] == [
        ("黑色折疊椅", 1),
        ("黑色折疊椅", 1),
        ("帳篷", 3),
        ("營釘", 2),
    ]
