"""排隊收購快速估價：每件在估價時直接選類型（docs/42 §13；店主 2026-10-02）。

二手（預設）／全新（買斷、成色全新）／散裝（整堆總價，件數可不填＝整堆 1 件）／
寄售（填寄售售價、當下必填；抽成用預設）。寄售品也要簽切結書，和其他商品列在同一份；
只賣寄售時不用選現金或購物金。
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
from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.signing.models import SignatureTask
from app.shared.enums import SignatureTaskStatus
from tests.integration.customer_display_helpers import signature_png_base64
from tests.integration.test_intake_payment import PATH, Ctx, _ctx, _pay


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


async def _batch(client: httpx.AsyncClient, ctx: Ctx, n: int) -> dict[str, Any]:
    resp = await client.post(
        PATH,
        json={"contact_id": ctx.contact_id, "declared_item_count": n, "prefill_lines": True},
        headers=ctx.auth,
    )
    assert resp.status_code == 201, resp.text
    batch: dict[str, Any] = resp.json()
    return batch


async def _patch(
    client: httpx.AsyncClient, ctx: Ctx, batch: dict[str, Any], i: int, fields: dict[str, Any]
) -> httpx.Response:
    line_id = batch["lines"][i]["id"]
    return await client.patch(
        f"{PATH}/{batch['id']}/lines/{line_id}", json=fields, headers=ctx.auth
    )


async def _get(client: httpx.AsyncClient, ctx: Ctx, batch_id: int) -> dict[str, Any]:
    batch: dict[str, Any] = (await client.get(f"{PATH}/{batch_id}", headers=ctx.auth)).json()
    return batch


async def _ready(client: httpx.AsyncClient, ctx: Ctx, batch_id: int) -> httpx.Response:
    return await client.post(f"{PATH}/{batch_id}/ready", headers=ctx.auth)


async def _confirm_and_start(
    client: httpx.AsyncClient, ctx: Ctx, batch_id: int, kept: list[int] | None = None
) -> dict[str, Any]:
    resp = await client.post(
        f"{PATH}/{batch_id}/customer-confirm", json={"kept_line_ids": kept or []}, headers=ctx.auth
    )
    assert resp.status_code == 200, resp.text
    started = await client.post(f"{PATH}/{batch_id}/tablet-signature", headers=ctx.auth)
    assert started.status_code == 200, started.text
    task: dict[str, Any] = started.json()
    return task


async def _sign(
    client: httpx.AsyncClient, ctx: Ctx, task_id: int, payout: str | None
) -> httpx.Response:
    body: dict[str, Any] = {
        "signature_image_base64": signature_png_base64(),
        "idempotency_key": f"k-{task_id}",
    }
    if payout is not None:
        body["chosen_payout"] = payout
    return await client.post(
        f"/api/v1/signing/tasks/{task_id}/tablet-sign", json=body, headers=ctx.auth
    )


# ── 選類型 ────────────────────────────────────────────────────────────


async def test_switching_to_consignment_uses_default_commission_and_drops_cost(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _batch(client, ctx, 1)
    await _patch(client, ctx, batch, 0, {"deal_cost": "300"})
    resp = await _patch(client, ctx, batch, 0, {"acquisition_type": "CONSIGNMENT"})
    assert resp.status_code == 200, resp.text
    line = resp.json()
    assert (line["commission_pct"], line["deal_cost"]) == (50, None)
    # 換回二手：抽成清掉
    back = await _patch(client, ctx, batch, 0, {"acquisition_type": "BUYOUT"})
    assert back.status_code == 200, back.text
    assert back.json()["commission_pct"] is None


async def test_consignment_price_is_required_before_ready(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _batch(client, ctx, 2)
    await _patch(client, ctx, batch, 0, {"deal_cost": "300"})
    await _patch(client, ctx, batch, 1, {"acquisition_type": "CONSIGNMENT"})
    assert (await _get(client, ctx, batch["id"]))["priced_item_count"] == 1  # 寄售還沒填售價
    blocked = await _ready(client, ctx, batch["id"])
    assert blocked.status_code == 409
    assert "售價" in blocked.json()["detail"]
    await _patch(client, ctx, batch, 1, {"expected_listed_price": "3000"})
    assert (await _get(client, ctx, batch["id"]))["priced_item_count"] == 2
    assert (await _ready(client, ctx, batch["id"])).status_code == 200


async def test_new_item_is_buyout_graded_new(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """「全新」＝買斷、成色直接是全新；付款後待整理商品已經有成色。"""
    ctx = await _ctx(db_session, client)
    batch = await _batch(client, ctx, 1)
    await _patch(client, ctx, batch, 0, {"acquisition_type": "BUYOUT", "grade": "N"})
    await _patch(client, ctx, batch, 0, {"deal_cost": "800"})
    assert (await _ready(client, ctx, batch["id"])).status_code == 200
    await client.post(
        f"{PATH}/{batch['id']}/customer-confirm", json={"kept_line_ids": []}, headers=ctx.auth
    )
    assert (await _pay(client, ctx, batch["id"])).status_code == 200
    item = await db_session.scalar(
        select(SerializedItem).where(SerializedItem.store_id == ctx.store_id)
    )
    assert item is not None and item.grade == "N"


# ── 散裝：整堆總價、件數可不填 ─────────────────────────────────────────


async def test_bulk_total_price_with_piece_count(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _batch(client, ctx, 1)
    resp = await _patch(
        client,
        ctx,
        batch,
        0,
        {
            "acquisition_type": "BULK_LOT",
            "short_name": "營釘",
            "deal_cost": "55",
            "bulk_piece_count": 10,
        },
    )
    assert resp.status_code == 200, resp.text
    assert (resp.json()["qty"], resp.json()["bulk_piece_count"]) == (1, 10)
    assert (await _ready(client, ctx, batch["id"])).status_code == 200
    ready = await _get(client, ctx, batch["id"])
    assert ready["deal_total"] == "55"  # 整堆總價，不是 55 × 10
    # 每件售價照每件成本（5.5）推算，不是照整堆
    assert int(ready["lines"][0]["expected_listed_price"]) < 55
    task = await _confirm_and_start(client, ctx, batch["id"])
    assert task["content"]["items"] == [{"name": "營釘 ×10", "amount": "55"}]
    assert task["content"]["total"] == "55"
    assert (await _sign(client, ctx, task["id"], "CASH")).status_code == 200
    assert (await _pay(client, ctx, batch["id"])).status_code == 200
    lot = await db_session.scalar(select(BulkLot).where(BulkLot.store_id == ctx.store_id))
    assert lot is not None
    assert (lot.total_qty, lot.remaining_qty, lot.acquisition_cost) == (10, 10, Decimal(55))


async def test_bulk_without_piece_count_is_one_pile(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _batch(client, ctx, 1)
    await _patch(
        client,
        ctx,
        batch,
        0,
        {"acquisition_type": "BULK_LOT", "short_name": "雜物", "deal_cost": "200"},
    )
    assert (await _ready(client, ctx, batch["id"])).status_code == 200
    task = await _confirm_and_start(client, ctx, batch["id"])
    assert task["content"]["items"] == [{"name": "雜物", "amount": "200"}]
    assert (await _sign(client, ctx, task["id"], "CASH")).status_code == 200
    assert (await _pay(client, ctx, batch["id"])).status_code == 200
    lot = await db_session.scalar(select(BulkLot).where(BulkLot.store_id == ctx.store_id))
    assert lot is not None and (lot.total_qty, lot.acquisition_cost) == (1, Decimal(200))


async def test_piece_count_only_for_bulk_and_cleared_when_switching_away(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _batch(client, ctx, 1)
    bad = await _patch(client, ctx, batch, 0, {"bulk_piece_count": 5})
    assert bad.status_code == 422
    await _patch(client, ctx, batch, 0, {"acquisition_type": "BULK_LOT", "bulk_piece_count": 5})
    back = await _patch(client, ctx, batch, 0, {"acquisition_type": "BUYOUT"})
    assert back.status_code == 200, back.text
    assert back.json()["bulk_piece_count"] is None


# ── 寄售也要簽切結 ────────────────────────────────────────────────────


async def _mixed(client: httpx.AsyncClient, ctx: Ctx) -> dict[str, Any]:
    batch = await _batch(client, ctx, 2)
    await _patch(client, ctx, batch, 0, {"deal_cost": "300", "short_name": "營燈"})
    await _patch(
        client,
        ctx,
        batch,
        1,
        {"acquisition_type": "CONSIGNMENT", "short_name": "帳篷", "expected_listed_price": "6000"},
    )
    assert (await _ready(client, ctx, batch["id"])).status_code == 200
    return batch


async def test_consignment_items_are_on_the_affidavit_but_not_in_total(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _mixed(client, ctx)
    task = await _confirm_and_start(client, ctx, batch["id"])
    content = task["content"]
    assert content["items"] == [{"name": "營燈", "amount": "300"}]
    assert content["total"] == "300"
    assert content["consignments"] == [
        {"name": "帳篷", "listed_price": "6000", "commission_pct": 50}
    ]
    assert (await _sign(client, ctx, task["id"], "CASH")).status_code == 200
    paid = await _pay(client, ctx, batch["id"])
    assert paid.status_code == 200, paid.text
    row = await db_session.get(SignatureTask, task["id"])
    assert row is not None
    await db_session.refresh(row)
    assert row.status is SignatureTaskStatus.CONSUMED


async def test_consignment_only_signs_without_choosing_payout(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _mixed(client, ctx)
    # 客人只留寄售（1 號營燈不賣）
    task = await _confirm_and_start(client, ctx, batch["id"], kept=[batch["lines"][0]["id"]])
    assert task["content"]["items"] == [] and task["content"]["total"] == "0"
    assert len(task["content"]["consignments"]) == 1
    signed = await _sign(client, ctx, task["id"], None)
    assert signed.status_code == 200, signed.text
    assert signed.json()["chosen_payout"] is None
    paid = await _pay(client, ctx, batch["id"])
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "PAID"
    row = await db_session.get(SignatureTask, task["id"])
    assert row is not None
    await db_session.refresh(row)
    assert row.status is SignatureTaskStatus.CONSUMED


async def test_paying_items_still_need_a_payout_choice(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _mixed(client, ctx)
    task = await _confirm_and_start(client, ctx, batch["id"])
    assert (await _sign(client, ctx, task["id"], None)).status_code == 422


async def test_changing_consignment_price_after_signing_requires_resigning(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _mixed(client, ctx)
    task = await _confirm_and_start(client, ctx, batch["id"])
    assert (await _sign(client, ctx, task["id"], "CASH")).status_code == 200
    await _patch(client, ctx, batch, 1, {"expected_listed_price": "5000"})
    resp = await _pay(client, ctx, batch["id"])
    assert resp.status_code == 409
    assert "重新簽署" in resp.json()["detail"]


# ── 收購明細（含簽名）也印寄售 ──────────────────────────────────────────


async def test_receipt_lists_consignments_with_mixed_batch(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _mixed(client, ctx)
    task = await _confirm_and_start(client, ctx, batch["id"])
    assert (await _sign(client, ctx, task["id"], "CASH")).status_code == 200
    assert (await _pay(client, ctx, batch["id"])).status_code == 200
    resp = await client.get(f"{PATH}/{batch['id']}/receipt", headers=ctx.auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == [{"name": "營燈", "amount": "300"}]
    assert body["total"] == "300" and body["payout_method"] == "CASH"
    assert body["consignments"] == [{"name": "帳篷", "listed_price": "6000", "commission_pct": 50}]


async def test_receipt_for_consignment_only_batch(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _mixed(client, ctx)
    task = await _confirm_and_start(client, ctx, batch["id"], kept=[batch["lines"][0]["id"]])
    assert (await _sign(client, ctx, task["id"], None)).status_code == 200
    assert (await _pay(client, ctx, batch["id"])).status_code == 200
    resp = await client.get(f"{PATH}/{batch['id']}/receipt", headers=ctx.auth)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["items"], body["total"], body["payout_method"]) == ([], "0", None)
    assert [c["name"] for c in body["consignments"]] == ["帳篷"]
