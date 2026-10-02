"""排隊收購快速估價（docs/42 §13；店主 2026-10-02）。

- 報到填「收購幾件」→ 自動建好那麼多件（每件一列、數量 1、預設買斷、名稱「第 N 件」）。
- 估價只填收購價就能估完；預計售價沒填就從收購價推算（CLAUDE.md §7.9），成色可以先空著。
- 估完時每件預設「要賣」；客人在平板上取消勾選的＝客人不賣、交還客人。
- 付款後「待整理」商品可以沒有成色，**上架時一定要選**；資料庫只容許待整理的商品沒成色。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.money import suggested_listed_price
from app.main import create_app
from app.modules.inventory.models import Category, SerializedItem
from app.modules.settings.service import StoreSettingsService
from app.shared.enums import SerializedItemStatus
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


async def _quick_batch(client: httpx.AsyncClient, ctx: Ctx, count: int = 3) -> dict[str, Any]:
    resp = await client.post(
        PATH,
        json={"contact_id": ctx.contact_id, "declared_item_count": count, "prefill_lines": True},
        headers=ctx.auth,
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def _price(
    client: httpx.AsyncClient, ctx: Ctx, batch: dict[str, Any], prices: list[str]
) -> dict[str, Any]:
    for line, price in zip(batch["lines"], prices, strict=True):
        resp = await client.patch(
            f"{PATH}/{batch['id']}/lines/{line['id']}", json={"deal_cost": price}, headers=ctx.auth
        )
        assert resp.status_code == 200, resp.text
    got = await client.get(f"{PATH}/{batch['id']}", headers=ctx.auth)
    result: dict[str, Any] = got.json()
    return result


async def _ready(client: httpx.AsyncClient, ctx: Ctx, batch_id: int) -> httpx.Response:
    return await client.post(f"{PATH}/{batch_id}/ready", headers=ctx.auth)


async def _confirm(
    client: httpx.AsyncClient, ctx: Ctx, batch_id: int, kept: list[int]
) -> httpx.Response:
    return await client.post(
        f"{PATH}/{batch_id}/customer-confirm", json={"kept_line_ids": kept}, headers=ctx.auth
    )


async def test_check_in_prefills_one_line_per_item(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _quick_batch(client, ctx, 3)
    assert batch["status"] == "PENDING_ESTIMATE"
    assert [
        (
            line["line_no"],
            line["short_name"],
            line["qty"],
            line["acquisition_type"],
            line["deal_cost"],
        )
        for line in batch["lines"]
    ] == [
        (1, "第 1 件", 1, "BUYOUT", None),
        (2, "第 2 件", 1, "BUYOUT", None),
        (3, "第 3 件", 1, "BUYOUT", None),
    ]


async def test_without_prefill_flag_nothing_changes(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    resp = await client.post(
        PATH, json={"contact_id": ctx.contact_id, "declared_item_count": 3}, headers=ctx.auth
    )
    assert resp.json()["lines"] == []


async def test_entering_a_price_starts_estimating(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _quick_batch(client, ctx, 2)
    assert (batch["item_count"], batch["priced_item_count"]) == (2, 0)
    after = await _price(client, ctx, {**batch, "lines": batch["lines"][:1]}, ["300"])
    assert after["status"] == "ESTIMATING"
    assert after["priced_item_count"] == 1


async def test_ready_with_only_prices_fills_listed_price_and_accepts_everything(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _quick_batch(client, ctx, 2)
    await _price(client, ctx, batch, ["300", "1000"])
    resp = await _ready(client, ctx, batch["id"])
    assert resp.status_code == 200, resp.text
    ready = resp.json()
    assert ready["status"] == "AWAITING_CONFIRM"
    # 跟系統用同一份實際生效的設定（沒存過設定的店用預設值）。
    settings = await StoreSettingsService(db_session).get_effective_settings(ctx.store_id)
    tax, margin = settings.tax_rate, settings.purchase_default_margin_pct
    fee = max(settings.linepay_fee_pct, settings.taiwanpay_fee_pct)
    for line, cost in zip(ready["lines"], (300, 1000), strict=True):
        expected = suggested_listed_price(Decimal(cost), margin, tax, fee)
        assert line["expected_listed_price"] == str(expected)
        assert line["grade"] is None
        assert (line["disposition"], line["accepted_qty"]) == ("ACCEPTED", 1)
    assert ready["accepted_total"] == "1300"


async def test_details_already_filled_are_kept(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _quick_batch(client, ctx, 1)
    line = batch["lines"][0]
    await client.patch(
        f"{PATH}/{batch['id']}/lines/{line['id']}",
        json={
            "deal_cost": "300",
            "expected_listed_price": "880",
            "grade": "A",
            "short_name": "營燈",
        },
        headers=ctx.auth,
    )
    ready = (await _ready(client, ctx, batch["id"])).json()
    assert (ready["lines"][0]["expected_listed_price"], ready["lines"][0]["grade"]) == ("880", "A")
    assert ready["lines"][0]["short_name"] == "營燈"


async def test_ready_requires_every_price(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _quick_batch(client, ctx, 3)
    await _price(client, ctx, {**batch, "lines": batch["lines"][:1]}, ["300"])
    resp = await _ready(client, ctx, batch["id"])
    assert resp.status_code == 409
    assert "第 2、3 件" in resp.json()["detail"]


async def test_consignment_still_needs_its_listed_price_and_commission(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _quick_batch(client, ctx, 1)
    line = batch["lines"][0]
    await client.patch(
        f"{PATH}/{batch['id']}/lines/{line['id']}",
        json={"acquisition_type": "CONSIGNMENT", "commission_pct": 50},
        headers=ctx.auth,
    )
    resp = await _ready(client, ctx, batch["id"])
    assert resp.status_code == 409
    assert "寄售" in resp.json()["detail"]


async def test_customer_unticks_items_they_keep(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _quick_batch(client, ctx, 3)
    await _price(client, ctx, batch, ["300", "500", "200"])
    await _ready(client, ctx, batch["id"])
    kept = batch["lines"][1]["id"]
    resp = await _confirm(client, ctx, batch["id"], [kept])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    states = {
        line["line_no"]: (line["disposition"], line["accepted_qty"], line["returned_to_customer"])
        for line in body["lines"]
    }
    assert states == {
        1: ("ACCEPTED", 1, False),
        2: ("CUSTOMER_KEPT", 0, True),
        3: ("ACCEPTED", 1, False),
    }
    assert body["accepted_total"] == "500"
    # 改主意再全勾回來
    again = (await _confirm(client, ctx, batch["id"], [])).json()
    assert again["accepted_total"] == "1000"


async def test_customer_keeping_everything_is_refused(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _quick_batch(client, ctx, 2)
    await _price(client, ctx, batch, ["300", "500"])
    await _ready(client, ctx, batch["id"])
    resp = await _confirm(client, ctx, batch["id"], [line["id"] for line in batch["lines"]])
    assert resp.status_code == 409
    assert "取消整批" in resp.json()["detail"]


async def test_customer_confirm_only_after_estimating(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _quick_batch(client, ctx, 1)
    assert (await _confirm(client, ctx, batch["id"], [])).status_code == 409


async def _paid_gradeless(
    client: httpx.AsyncClient, ctx: Ctx, db_session: AsyncSession
) -> tuple[dict[str, Any], dict[str, Any], SerializedItem]:
    batch = await _quick_batch(client, ctx, 1)
    await _price(client, ctx, batch, ["300"])
    await _ready(client, ctx, batch["id"])
    paid = await _pay(client, ctx, batch["id"])
    assert paid.status_code == 200, paid.text
    item = await db_session.scalar(
        select(SerializedItem).where(SerializedItem.store_id == ctx.store_id)
    )
    assert item is not None
    category = Category(store_id=ctx.store_id, name="燈具", target_margin_pct=45)
    db_session.add(category)
    await db_session.flush()
    items = (await client.get(f"{PATH}/{batch['id']}/items", headers=ctx.auth)).json()
    edit = {"kind": "SERIALIZED", "id": items[0]["id"], "name": "營燈", "category_id": category.id}
    return batch, {"items": items, "edit": edit}, item


async def test_paid_item_without_grade_waits_for_listing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch, got, item = await _paid_gradeless(client, ctx, db_session)
    assert (item.status, item.grade) == (SerializedItemStatus.PENDING_LISTING, None)
    assert got["items"][0]["missing"][0] == "成色"
    ok = await client.post(
        f"{PATH}/{batch['id']}/listing",
        json={"items": [{**got["edit"], "grade": "B"}], "publish": True},
        headers=ctx.auth,
    )
    assert ok.status_code == 200, ok.text
    await db_session.refresh(item)
    assert (item.status, item.grade) == (SerializedItemStatus.IN_STOCK, "B")


async def test_listing_without_grade_is_refused(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch, got, _ = await _paid_gradeless(client, ctx, db_session)
    # 失敗的請求會回滾整個測試交易，所以放最後一步（docs/50 §8）。
    no_grade = await client.post(
        f"{PATH}/{batch['id']}/listing",
        json={"items": [got["edit"]], "publish": True},
        headers=ctx.auth,
    )
    assert no_grade.status_code == 422
    assert "成色" in no_grade.json()["detail"]


async def test_database_refuses_in_stock_item_without_grade(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch = await _quick_batch(client, ctx, 1)
    await _price(client, ctx, batch, ["300"])
    await _ready(client, ctx, batch["id"])
    assert (await _pay(client, ctx, batch["id"])).status_code == 200
    item = await db_session.scalar(
        select(SerializedItem).where(SerializedItem.store_id == ctx.store_id)
    )
    assert item is not None
    item.status = SerializedItemStatus.IN_STOCK
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_inventory_list_and_detail_show_gradeless_pending_item(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """沒成色的待整理商品出現在庫存清單與明細時不能讓整頁出錯。"""
    ctx = await _ctx(db_session, client)
    _, _, item = await _paid_gradeless(client, ctx, db_session)
    listed = await client.get(
        "/api/v1/serialized-items", params={"status": "PENDING_LISTING"}, headers=ctx.auth
    )
    assert listed.status_code == 200, listed.text
    assert [(i["id"], i["grade"]) for i in listed.json()] == [(item.id, None)]
    detail = await client.get(f"/api/v1/serialized-items/{item.id}/detail", headers=ctx.auth)
    assert detail.status_code == 200, detail.text
    assert detail.json()["grade"] is None
