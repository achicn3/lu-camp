"""R2 銷售 / 毛利報表整合測試（docs/19 §2.3）：

買斷序號毛利=售價−成本；散裝 per-piece 成本逐行四捨五入；寄售只認抽成、不認全額；
catalog 成本未知 → unknown_cost_sales、不假造毛利；作廢不計；唯讀；MANAGER；匯出。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import BulkLot, CatalogProduct, SerializedItem
from app.modules.menu.models import MenuItem
from app.modules.sales.models import Sale
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    Grade,
    OwnershipType,
    SerializedItemStatus,
    UserRole,
)

_WIDE = {"from": "2000-01-01T00:00:00Z", "to": "2100-01-01T00:00:00Z"}


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


async def _seed(session: AsyncSession) -> tuple[str, str, int, int, int]:
    """建店＋MANAGER＋CLERK（開帳）＋寄售人，回 (mgr, clerk, store_id, consignor_id, clerk_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    consignor = Contact(store_id=store.id, name="寄售人", roles=["SELLER"], national_id_enc="enc")
    session.add_all([mgr, clerk, consignor])
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal(1000))
    mgr_token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
    clerk_token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return mgr_token, clerk_token, store.id, consignor.id, clerk.id


def _auth(token: str, *, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


async def _add_owned_serialized(
    session: AsyncSession, store_id: int, *, code: str, cost: str, price: str
) -> None:
    session.add(
        SerializedItem(
            store_id=store_id,
            item_code=code,
            name="自有序號品",
            grade=Grade.A,
            ownership_type=OwnershipType.OWNED,
            acquisition_cost=Decimal(cost),
            listed_price=Decimal(price),
            status=SerializedItemStatus.IN_STOCK,
        )
    )
    await session.flush()


async def _add_consignment_serialized(
    session: AsyncSession,
    store_id: int,
    consignor_id: int,
    *,
    code: str,
    price: str,
    commission_pct: int,
) -> None:
    session.add(
        SerializedItem(
            store_id=store_id,
            item_code=code,
            name="寄售序號品",
            grade=Grade.A,
            ownership_type=OwnershipType.CONSIGNMENT,
            consignor_id=consignor_id,
            commission_pct=commission_pct,
            listed_price=Decimal(price),
            status=SerializedItemStatus.IN_STOCK,
        )
    )
    await session.flush()


async def _add_bulk(
    session: AsyncSession,
    store_id: int,
    *,
    cost: str,
    total_qty: int,
    unit_price: str,
) -> int:
    lot = BulkLot(
        store_id=store_id,
        lot_code=f"L-{store_id}-{cost}",
        name="自有散裝",
        grade=Grade.E,
        acquisition_cost=Decimal(cost),
        acquisition_basis=BulkAcquisitionBasis.BAG,
        unit_price=Decimal(unit_price),
        total_qty=total_qty,
        remaining_qty=total_qty,
        status=BulkLotStatus.ON_SALE,
    )
    session.add(lot)
    await session.flush()
    return lot.id


async def _add_catalog(session: AsyncSession, store_id: int, *, price: str, qty: int) -> int:
    product = CatalogProduct(
        store_id=store_id,
        sku=f"SKU-{price}",
        name="數量品",
        unit_price=Decimal(price),
        quantity_on_hand=qty,
    )
    session.add(product)
    await session.flush()
    return product.id


async def _add_menu(session: AsyncSession, store_id: int, *, name: str, price: str) -> int:
    item = MenuItem(store_id=store_id, name=name, unit_price=Decimal(price))
    session.add(item)
    await session.flush()
    return item.id


async def _sell(
    client: httpx.AsyncClient, token: str, lines: list[dict[str, object]], *, key: str
) -> dict[str, object]:
    resp = await client.post("/api/v1/sales", json={"lines": lines}, headers=_auth(token, idem=key))
    assert resp.status_code == 201, resp.text
    body: dict[str, object] = resp.json()
    return body


async def _margin(client: httpx.AsyncClient, mgr: str) -> dict[str, object]:
    resp = await client.get("/api/v1/reports/sales-margin", params=_WIDE, headers=_auth(mgr))
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    return body


async def test_owned_serialized_margin(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, clerk, store_id, _consignor, _clerk_id = await _seed(db_session)
    await _add_owned_serialized(db_session, store_id, code="OWN-1", cost="300", price="500")
    await _sell(client, clerk, [{"line_type": "SERIALIZED", "item_code": "OWN-1"}], key="o1")

    body = await _margin(client, mgr)
    assert body["gross_turnover"] == "500"
    assert body["recognized_revenue"] == "500"
    assert body["owned_cogs"] == "300"
    assert body["gross_margin"] == "200"  # 500 - 300
    assert body["gross_margin_rate"] == "0.4000"  # 200 / 500
    assert body["unknown_cost_sales"] == "0"
    assert body["transaction_count"] == 1


async def test_menu_revenue_recognized_and_split(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """餐飲：全額認列、計入 gross/recognized 但成本未知→unknown_cost；report 分列餐飲/二手。"""
    mgr, clerk, store_id, _consignor, _clerk_id = await _seed(db_session)
    await _add_owned_serialized(db_session, store_id, code="OWN-1", cost="300", price="500")
    menu_id = await _add_menu(db_session, store_id, name="手沖-耶加", price="180")
    await _sell(
        client,
        clerk,
        [
            {"line_type": "SERIALIZED", "item_code": "OWN-1"},
            {"line_type": "MENU", "menu_item_id": menu_id, "qty": 2},
        ],
        key="mix1",
    )

    body = await _margin(client, mgr)
    assert body["gross_turnover"] == "860"  # 500 二手 + 360 餐飲
    assert body["recognized_revenue"] == "860"  # 餐飲全額認列
    assert body["food_revenue"] == "360"
    assert body["secondhand_revenue"] == "500"  # recognized − food
    assert body["unknown_cost_sales"] == "360"  # 餐飲無成本，不灌毛利
    assert body["gross_margin"] == "200"  # 僅二手 500−300
    assert body["gross_margin_rate"] == "0.4000"  # 餐飲排除於分母（200/500）


async def test_consignment_recognizes_commission_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, store_id, consignor, _clerk_id = await _seed(db_session)
    await _add_consignment_serialized(
        db_session, store_id, consignor, code="CON-1", price="1000", commission_pct=50
    )
    await _sell(client, clerk, [{"line_type": "SERIALIZED", "item_code": "CON-1"}], key="c1")

    body = await _margin(client, mgr)
    # 營業額認全額 1000；認列營收與毛利只認抽成 500
    assert body["gross_turnover"] == "1000"
    assert body["consignment_commission_income"] == "500"
    assert body["recognized_revenue"] == "500"
    assert body["gross_margin"] == "500"
    assert body["owned_cogs"] == "0"
    assert body["gross_margin_rate"] == "1.0000"  # 500 / 500（抽成為純利）


async def test_catalog_is_unknown_cost_not_fake_margin(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, store_id, _consignor, _clerk_id = await _seed(db_session)
    cat = await _add_catalog(db_session, store_id, price="100", qty=10)
    await _sell(
        client, clerk, [{"line_type": "CATALOG", "catalog_product_id": cat, "qty": 2}], key="k1"
    )

    body = await _margin(client, mgr)
    assert body["gross_turnover"] == "200"
    assert body["recognized_revenue"] == "200"
    assert body["unknown_cost_sales"] == "200"
    assert body["gross_margin"] == "0"  # 成本未知 → 不假造毛利
    assert body["gross_margin_rate"] is None  # 已知成本營收為 0 → N/A


async def test_bulk_cogs_rounds_per_line(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """散裝每件成本 = round_ntd(1000 × 1 ÷ 3) = 333（HALF_UP）；毛利 = 售價 500 − 333 = 167。"""
    mgr, clerk, store_id, _consignor, _clerk_id = await _seed(db_session)
    lot = await _add_bulk(db_session, store_id, cost="1000", total_qty=3, unit_price="500")
    await _sell(client, clerk, [{"line_type": "BULK_LOT", "bulk_lot_id": lot, "qty": 1}], key="b1")

    body = await _margin(client, mgr)
    assert body["gross_turnover"] == "500"
    assert body["bulk_cogs"] == "333"
    assert body["gross_margin"] == "167"


async def test_voided_sale_excluded(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, clerk, store_id, _consignor, _clerk_id = await _seed(db_session)
    await _add_owned_serialized(db_session, store_id, code="OWN-V", cost="300", price="500")
    sale = await _sell(client, clerk, [{"line_type": "SERIALIZED", "item_code": "OWN-V"}], key="v1")
    voided = await client.post(f"/api/v1/sales/{sale['id']}/void", headers=_auth(mgr))
    assert voided.status_code == 200, voided.text

    body = await _margin(client, mgr)
    assert body["gross_turnover"] == "0"
    assert body["gross_margin"] == "0"
    assert body["transaction_count"] == 0


async def test_sales_margin_is_read_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, store_id, _consignor, _clerk_id = await _seed(db_session)
    await _add_owned_serialized(db_session, store_id, code="OWN-R", cost="300", price="500")
    await _sell(client, clerk, [{"line_type": "SERIALIZED", "item_code": "OWN-R"}], key="r1")

    before = await db_session.scalar(select(func.count()).select_from(Sale))
    await _margin(client, mgr)
    after = await db_session.scalar(select(func.count()).select_from(Sale))
    assert before == after


async def test_sales_margin_rejects_bad_range_and_clerk(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, _store_id, _consignor, _clerk_id = await _seed(db_session)
    bad = await client.get(
        "/api/v1/reports/sales-margin",
        params={"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        headers=_auth(mgr),
    )
    assert bad.status_code == 422
    forbidden = await client.get("/api/v1/reports/sales-margin", params=_WIDE, headers=_auth(clerk))
    assert forbidden.status_code == 403


async def test_sales_margin_csv_export(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, clerk, store_id, _consignor, _clerk_id = await _seed(db_session)
    await _add_owned_serialized(db_session, store_id, code="OWN-C", cost="300", price="500")
    await _sell(client, clerk, [{"line_type": "SERIALIZED", "item_code": "OWN-C"}], key="csv1")

    resp = await client.get(
        "/api/v1/reports/sales-margin",
        params={**_WIDE, "format": "csv"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 200
    text = resp.content.decode("utf-8-sig")
    assert "毛利" in text and "營業額" in text and "毛利率" in text
