"""餐飲交易清單 API（docs/47 §4）：只列含餐點的交易，附餐點摘要、小計與已退金額。"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.inventory.models import CatalogProduct
from app.modules.menu.service import MenuService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole
from tests.integration.customer_display_helpers import CustomerDisplayAwareClient


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app()

    async def _override() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with CustomerDisplayAwareClient(
        transport=transport, base_url="http://test", db_session=db_session
    ) as c:
        yield c
    app.dependency_overrides.clear()


class _Ctx:
    token = ""
    store_id = 0
    latte = 0
    cake = 0
    catalog = 0


async def _seed(session: AsyncSession, name: str = "門市") -> _Ctx:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username=f"c-{name}", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    menu = MenuService(session)
    latte = await menu.create_menu_item(
        store.id, name="拿鐵", unit_price=Decimal(150), actor_user_id=clerk.id
    )
    cake = await menu.create_menu_item(
        store.id, name="戚風", unit_price=Decimal(90), actor_user_id=clerk.id
    )
    product = CatalogProduct(
        store_id=store.id, sku=f"S-{name}", name="營燈", unit_price=Decimal(500), quantity_on_hand=5
    )
    session.add(product)
    await session.flush()
    c = _Ctx()
    c.token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    c.store_id, c.latte, c.cake, c.catalog = store.id, latte.id, cake.id, product.id
    return c


def _h(c: _Ctx, idem: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {c.token}"}
    if idem:
        h["Idempotency-Key"] = idem
    return h


async def _sell(
    client: httpx.AsyncClient, c: _Ctx, lines: list[dict[str, object]], idem: str, **extra: object
) -> dict[str, object]:
    resp = await client.post("/api/v1/sales", json={"lines": lines, **extra}, headers=_h(c, idem))
    assert resp.status_code == 201, resp.text
    body: dict[str, object] = resp.json()
    return body


async def test_lists_only_sales_with_food(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    c = await _seed(db_session)
    await _sell(
        client, c, [{"line_type": "CATALOG", "catalog_product_id": c.catalog, "qty": 1}], "a"
    )
    mixed = await _sell(
        client,
        c,
        [
            {"line_type": "MENU", "menu_item_id": c.latte, "qty": 2},
            {"line_type": "MENU", "menu_item_id": c.cake, "qty": 1},
            {"line_type": "CATALOG", "catalog_product_id": c.catalog, "qty": 1},
        ],
        "b",
        service_mode="TAKEOUT",
    )
    resp = await client.get("/api/v1/sales/fnb", headers=_h(c))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["id"] for r in rows] == [mixed["id"]]
    row = rows[0]
    assert row["food_items"] == "拿鐵×2、戚風×1"
    assert row["food_subtotal"] == "390"
    assert row["total"] == "890"
    assert row["has_other_items"] is True
    assert row["food_refunded"] == "0"
    assert row["total_refunded"] == "0"
    assert row["service_mode"] == "TAKEOUT"


async def test_shows_refunded_amounts(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    c = await _seed(db_session)
    sale = await _sell(
        client,
        c,
        [{"line_type": "MENU", "menu_item_id": c.latte, "qty": 2}],
        "r",
        service_mode="TAKEOUT",
    )
    lines = sale["lines"]
    assert isinstance(lines, list)
    ret = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "做錯",
            "lines": [{"sale_line_id": lines[0]["id"], "qty": 1}],
        },
        headers=_h(c, "ret-1"),
    )
    assert ret.status_code == 201, ret.text
    row = (await client.get("/api/v1/sales/fnb", headers=_h(c))).json()[0]
    assert (row["food_refunded"], row["total_refunded"], row["has_other_items"]) == (
        "150",
        "150",
        False,
    )


async def test_store_scoped(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    a = await _seed(db_session, "A")
    b = await _seed(db_session, "B")
    await _sell(
        client,
        a,
        [{"line_type": "MENU", "menu_item_id": a.latte, "qty": 1}],
        "s",
        service_mode="TAKEOUT",
    )
    assert (await client.get("/api/v1/sales/fnb", headers=_h(b))).json() == []


async def test_date_range(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    c = await _seed(db_session)
    await _sell(
        client,
        c,
        [{"line_type": "MENU", "menu_item_id": c.latte, "qty": 1}],
        "d",
        service_mode="TAKEOUT",
    )
    resp = await client.get(
        "/api/v1/sales/fnb",
        params={"from": "2020-01-01T00:00:00+08:00", "to": "2020-01-02T00:00:00+08:00"},
        headers=_h(c),
    )
    assert resp.status_code == 200
    assert resp.json() == []
