"""估價「詳細」的商品簡稱自動完成（店主 2026-10-14）。

打過的簡稱再打時列出來，附上次估的原價／預計售價／收購價；同名不同品牌分開列、帶品牌名
（品牌多半是上架時才補在商品上，要從那一列建出的商品推回來）。快速估價的預設名「第 N 件」不算。
"""

from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.main import create_app
from app.modules.inventory.models import Brand
from tests.integration.test_intake_payment import (
    PATH,
    Ctx,
    _confirmed_batch,
    _ctx,
    _line,
    _pay,
)

SUGGEST = f"{PATH}/short-name-suggestions"


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


async def _batch(client: httpx.AsyncClient, ctx: Ctx) -> int:
    resp = await client.post(
        PATH, json={"contact_id": ctx.contact_id, "declared_item_count": 1}, headers=ctx.auth
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _add(client: httpx.AsyncClient, ctx: Ctx, batch_id: int, **fields: object) -> int:
    resp = await client.post(f"{PATH}/{batch_id}/lines", json=_line(**fields), headers=ctx.auth)
    assert resp.status_code == 201, resp.text
    return int(resp.json()["id"])


async def _suggest(
    client: httpx.AsyncClient, ctx: Ctx, q: str, **params: int
) -> list[dict[str, Any]]:
    resp = await client.get(SUGGEST, params={"q": q, **params}, headers=ctx.auth)
    assert resp.status_code == 200, resp.text
    rows: list[dict[str, Any]] = resp.json()
    return rows


async def _brand(db: AsyncSession, ctx: Ctx, name: str) -> int:
    brand = Brand(store_id=ctx.store_id, name=name)
    db.add(brand)
    await db.flush()
    return brand.id


async def test_lists_past_short_names_with_their_last_estimate(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch_id = await _batch(client, ctx)
    await _add(client, ctx, batch_id, short_name="黑色折疊椅", deal_cost="200")
    await _add(client, ctx, batch_id, short_name="黑色折疊椅", deal_cost="250")
    await _add(client, ctx, batch_id, short_name="帳篷", reference_price="5000")

    rows = await _suggest(client, ctx, "折疊")

    assert len(rows) == 1
    chair = rows[0]
    assert chair["short_name"] == "黑色折疊椅"
    assert chair["brand_name"] is None
    assert chair["times"] == 2
    # 最近一次的估價
    assert chair["reference_price"] == "1000"
    assert chair["discount_pct"] == 50  # 「帶入」要連折數一起帶
    assert chair["expected_listed_price"] == "500"
    assert chair["deal_cost"] == "250"
    assert chair["acquisition_type"] == "BUYOUT"
    assert chair["estimated_on"]


async def test_prefix_matches_come_first(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    batch_id = await _batch(client, ctx)
    await _add(client, ctx, batch_id, short_name="椅子")
    await _add(client, ctx, batch_id, short_name="黑色折疊椅")

    rows = await _suggest(client, ctx, "椅")

    assert [r["short_name"] for r in rows] == ["椅子", "黑色折疊椅"]


async def test_placeholder_names_and_the_line_being_edited_are_not_suggested(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    resp = await client.post(
        PATH,
        json={"contact_id": ctx.contact_id, "declared_item_count": 2, "prefill_lines": True},
        headers=ctx.auth,
    )
    assert resp.status_code == 201, resp.text
    batch_id = int(resp.json()["id"])
    editing = await _add(client, ctx, batch_id, short_name="營燈")

    assert await _suggest(client, ctx, "件") == []
    assert await _suggest(client, ctx, "營燈", exclude_line_id=editing) == []
    assert [r["short_name"] for r in await _suggest(client, ctx, "營燈")] == ["營燈"]


async def test_same_name_different_brand_is_listed_separately_with_brand(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """品牌在上架時補在商品上：從那一列建出的商品推回品牌，同名不同品牌分兩筆。"""
    ctx = await _ctx(db_session, client)
    coleman = await _brand(db_session, ctx, "Coleman")
    snow_peak = await _brand(db_session, ctx, "Snow Peak")
    # 一批：估價列上直接帶品牌
    await _confirmed_batch(client, ctx, [_line(qty=1, deal_cost="300", brand_id=snow_peak)], [1])
    # 另一批：付款後在上架時才補品牌
    batch = await _confirmed_batch(client, ctx, [_line(qty=1, deal_cost="250")], [1])
    assert (await _pay(client, ctx, batch["id"])).status_code == 200
    item = (await client.get(f"{PATH}/{batch['id']}/items", headers=ctx.auth)).json()[0]
    resp = await client.post(
        f"{PATH}/{batch['id']}/listing",
        json={"items": [{"kind": "SERIALIZED", "id": item["id"], "brand_id": coleman}]},
        headers=ctx.auth,
    )
    assert resp.status_code == 200, resp.text

    rows = await _suggest(client, ctx, "折疊椅")

    by_brand = {r["brand_name"]: r for r in rows}
    assert set(by_brand) == {"Coleman", "Snow Peak"}
    assert by_brand["Coleman"]["deal_cost"] == "250"
    assert by_brand["Snow Peak"]["deal_cost"] == "300"
    assert all(r["short_name"] == "黑色折疊椅" for r in rows)


async def test_other_stores_names_are_not_suggested(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mine = await _ctx(db_session, client)
    other = await _ctx(db_session, client)
    await _add(client, other, await _batch(client, other), short_name="別家的椅子")

    assert await _suggest(client, mine, "椅子") == []


async def test_like_wildcards_are_matched_literally(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    ctx = await _ctx(db_session, client)
    await _add(client, ctx, await _batch(client, ctx), short_name="椅子")

    assert await _suggest(client, ctx, "%") == []
    assert await _suggest(client, ctx, "_") == []
    assert await _suggest(client, ctx, "  ") == []


async def _listed_as(
    client: httpx.AsyncClient, ctx: Ctx, batch_id: int, brand_id: int, model_name: str
) -> int:
    """付款後在上架時補品牌與型號（型號名稱建在該品牌底下）。"""
    model = await client.post(
        "/api/v1/product-models",
        json={"brand_id": brand_id, "name": model_name},
        headers=ctx.auth,
    )
    assert model.status_code in (200, 201), model.text
    item = (await client.get(f"{PATH}/{batch_id}/items", headers=ctx.auth)).json()[0]
    resp = await client.post(
        f"{PATH}/{batch_id}/listing",
        json={
            "items": [
                {
                    "kind": "SERIALIZED",
                    "id": item["id"],
                    "brand_id": brand_id,
                    "product_model_id": model.json()["id"],
                }
            ]
        },
        headers=ctx.auth,
    )
    assert resp.status_code == 200, resp.text
    return int(model.json()["id"])


async def test_listed_model_is_searchable_and_shows_brand(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """打上架時選的型號也找得到（店主 2026-10-15），而且一定帶品牌。"""
    ctx = await _ctx(db_session, client)
    snow_peak = await _brand(db_session, ctx, "Snow Peak")
    batch = await _confirmed_batch(client, ctx, [_line(short_name="黑椅子", qty=1)], [1])
    assert (await _pay(client, ctx, batch["id"])).status_code == 200
    model_id = await _listed_as(client, ctx, batch["id"], snow_peak, "FD 椅")

    rows = await _suggest(client, ctx, "FD")

    assert len(rows) == 1
    row = rows[0]
    assert row["short_name"] == "黑椅子"
    assert row["product_model_id"] == model_id
    assert row["product_model_name"] == "FD 椅"
    assert row["brand_name"] == "Snow Peak"
    assert row["deal_cost"] == "250"
    # 打簡稱也一樣帶出型號
    assert (await _suggest(client, ctx, "黑椅"))[0]["product_model_name"] == "FD 椅"


async def test_unnamed_line_listed_as_model_uses_model_name(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """快速估價沒取簡稱（「第 N 件」）、上架時選了型號：建議用型號名稱當簡稱。"""
    ctx = await _ctx(db_session, client)
    coleman = await _brand(db_session, ctx, "Coleman")
    batch = await _confirmed_batch(client, ctx, [_line(short_name="第 1 件", qty=1)], [1])
    assert (await _pay(client, ctx, batch["id"])).status_code == 200
    await _listed_as(client, ctx, batch["id"], coleman, "焚火台 M")

    rows = await _suggest(client, ctx, "焚火")

    assert [(r["short_name"], r["brand_name"], r["product_model_name"]) for r in rows] == [
        ("焚火台 M", "Coleman", "焚火台 M")
    ]


async def test_listed_brand_and_model_replace_the_estimate_pair(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """估價列帶品牌 A、上架改成品牌 B＋型號 B：建議要整組用上架的，不能變成 A＋B（Codex P2）。"""
    ctx = await _ctx(db_session, client)
    coleman = await _brand(db_session, ctx, "Coleman")
    snow_peak = await _brand(db_session, ctx, "Snow Peak")
    batch = await _confirmed_batch(
        client, ctx, [_line(short_name="營燈", qty=1, brand_id=coleman)], [1]
    )
    assert (await _pay(client, ctx, batch["id"])).status_code == 200
    await _listed_as(client, ctx, batch["id"], snow_peak, "GL 營燈")

    [row] = await _suggest(client, ctx, "營燈")

    assert (row["brand_name"], row["product_model_name"]) == ("Snow Peak", "GL 營燈")
