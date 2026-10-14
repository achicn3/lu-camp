"""餐飲品項報表（店主 2026-10-15）：期間內哪個品項賣得好、排名、和上一期比、哪些沒賣出。

品項的份數與營收扣退款（依退款日）；毛利只算有填成本的品項；上一期＝緊接在前、同樣長的期間。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.menu.models import MenuItem
from app.modules.sales.models import Sale
from tests.integration.test_reports_sales_margin import (
    _add_menu,
    _auth,
    _seed,
    _sell,
    client,
)

__all__ = ["client"]

PATH = "/api/v1/reports/menu-items"


def _window(days_back: int, days: int) -> dict[str, str]:
    end = datetime.now(UTC) + timedelta(hours=1) - timedelta(days=days_back)
    start = end - timedelta(days=days)
    return {"from": start.isoformat(), "to": end.isoformat()}


async def _report(client: httpx.AsyncClient, token: str, params: dict[str, str]) -> dict[str, Any]:
    resp = await client.get(PATH, params=params, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def test_ranks_items_and_lists_unsold(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, store_id, _consignor, _clerk_id = await _seed(db_session)
    latte = await _add_menu(db_session, store_id, name="拿鐵", price="150", cost="45")
    cake = await _add_menu(db_session, store_id, name="蛋糕", price="180")
    await _add_menu(db_session, store_id, name="紅茶", price="80")
    hidden = await _add_menu(db_session, store_id, name="季節限定", price="200")
    await db_session.execute(
        update(MenuItem).where(MenuItem.id == hidden).values(is_available=False)
    )
    await _sell(
        client,
        clerk,
        [
            {"line_type": "MENU", "menu_item_id": latte, "qty": 2},
            {"line_type": "MENU", "menu_item_id": cake, "qty": 1},
        ],
        key="m1",
    )
    await _sell(client, clerk, [{"line_type": "MENU", "menu_item_id": latte, "qty": 1}], key="m2")

    body = await _report(client, mgr, _window(0, 7))

    rows = [
        (r["rank"], r["name"], r["units"], r["revenue"], r["order_count"]) for r in body["rows"]
    ]
    assert rows == [(1, "拿鐵", 3, "450", 2), (2, "蛋糕", 1, "180", 1)]
    latte_row, cake_row = body["rows"]
    assert latte_row["margin"] == "315"  # 450 − 45×3
    assert latte_row["margin_rate"] == "0.7000"
    assert latte_row["revenue_share"] == "0.7143"  # 450 ÷ 630
    assert cake_row["margin"] is None  # 沒填成本，不假造毛利
    assert [i["name"] for i in body["not_sold"]] == ["紅茶"]  # 已下架的不列
    summary = body["summary"]
    assert summary["units"] == 4
    assert summary["revenue"] == "630"
    assert summary["order_count"] == 2


async def test_refunds_are_netted(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, clerk, store_id, _consignor, _clerk_id = await _seed(db_session)
    latte = await _add_menu(db_session, store_id, name="拿鐵", price="150", cost="45")
    sale = await _sell(
        client, clerk, [{"line_type": "MENU", "menu_item_id": latte, "qty": 3}], key="r1"
    )
    lines = sale["lines"]
    assert isinstance(lines, list)
    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "做錯一杯",
            "lines": [{"sale_line_id": lines[0]["id"], "qty": 1, "resellable": True}],
        },
        headers=_auth(clerk, idem="r1-return"),
    )
    assert resp.status_code == 201, resp.text

    row = (await _report(client, mgr, _window(0, 7)))["rows"][0]
    assert row["units"] == 2
    assert row["revenue"] == "300"
    assert row["margin"] == "210"


async def test_compares_with_previous_period(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, store_id, _consignor, _clerk_id = await _seed(db_session)
    latte = await _add_menu(db_session, store_id, name="拿鐵", price="150")
    cake = await _add_menu(db_session, store_id, name="蛋糕", price="180")
    old = await _sell(
        client, clerk, [{"line_type": "MENU", "menu_item_id": cake, "qty": 5}], key="p1"
    )
    await db_session.execute(
        update(Sale)
        .where(Sale.id == old["id"])
        .values(created_at=datetime.now(UTC) - timedelta(days=10))
    )
    await _sell(client, clerk, [{"line_type": "MENU", "menu_item_id": latte, "qty": 2}], key="p2")
    await _sell(client, clerk, [{"line_type": "MENU", "menu_item_id": cake, "qty": 1}], key="p3")

    body = await _report(client, mgr, _window(0, 7))

    by_name = {r["name"]: r for r in body["rows"]}
    assert by_name["拿鐵"]["prev_units"] == 0
    assert by_name["拿鐵"]["prev_rank"] is None  # 上一期沒賣
    assert by_name["蛋糕"]["prev_units"] == 5
    assert by_name["蛋糕"]["prev_rank"] == 1
    assert body["summary"]["prev_units"] == 5
    assert body["summary"]["prev_revenue"] == "900"


async def test_manager_only(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _mgr, clerk, _store_id, _consignor, _clerk_id = await _seed(db_session)
    resp = await client.get(PATH, params=_window(0, 1), headers=_auth(clerk))
    assert resp.status_code == 403
