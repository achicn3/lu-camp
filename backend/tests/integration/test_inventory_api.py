"""inventory 唯讀查詢 API 整合測試（T19-pre-B）。

POS/庫存頁前置：掃碼查件（by-code）、序號品/數量品/散裝堆列表。
全部需認證、以 token 的 store_id 範圍過濾（§4）；金額一律字串整數元（§6/§11）。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import BulkLot, CatalogProduct, SerializedItem
from app.modules.store.models import Store
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    Grade,
    OwnershipType,
    SerializedItemStatus,
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


async def _seed_store(session: AsyncSession, name: str = "測試門市") -> int:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    return store.id


def _auth(store_id: int) -> dict[str, str]:
    token = encode_access_token(user_id=1, role="CLERK", store_id=store_id)
    return {"Authorization": f"Bearer {token}"}


async def _seed_item(
    session: AsyncSession,
    store_id: int,
    *,
    item_code: str = "ITM-0001",
    status: SerializedItemStatus = SerializedItemStatus.IN_STOCK,
    ownership: OwnershipType = OwnershipType.OWNED,
    listed_price: str = "1280",
) -> SerializedItem:
    item = SerializedItem(
        store_id=store_id,
        item_code=item_code,
        name="雙人帳篷",
        grade=Grade.A,
        ownership_type=ownership,
        listed_price=Decimal(listed_price),
        status=status,
    )
    session.add(item)
    await session.flush()
    return item


async def test_get_serialized_by_code(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    store_id = await _seed_store(db_session)
    await _seed_item(db_session, store_id)
    resp = await client.get("/api/v1/serialized-items/by-code/ITM-0001", headers=_auth(store_id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["item_code"] == "ITM-0001"
    assert body["name"] == "雙人帳篷"
    assert body["listed_price"] == "1280"  # 金額為字串（§6/§11）
    assert body["status"] == "IN_STOCK"
    assert body["ownership_type"] == "OWNED"
    assert body["grade"] == "A"
    assert "acquisition_cost" not in body  # 成本不入一般查詢回應


async def test_by_code_is_store_scoped(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """他店的件以本店 token 查 → 404（§4 範圍過濾，不洩漏跨店資料）。"""
    store_a = await _seed_store(db_session, "A 店")
    store_b = await _seed_store(db_session, "B 店")
    await _seed_item(db_session, store_a)
    resp = await client.get("/api/v1/serialized-items/by-code/ITM-0001", headers=_auth(store_b))
    assert resp.status_code == 404


async def test_by_code_unknown_404(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    store_id = await _seed_store(db_session)
    resp = await client.get("/api/v1/serialized-items/by-code/NOPE", headers=_auth(store_id))
    assert resp.status_code == 404


async def test_list_serialized_filters_status_and_ownership(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(db_session)
    await _seed_item(db_session, store_id, item_code="ITM-IN", status=SerializedItemStatus.IN_STOCK)
    await _seed_item(db_session, store_id, item_code="ITM-SOLD", status=SerializedItemStatus.SOLD)
    await _seed_item(
        db_session, store_id, item_code="ITM-CONS", ownership=OwnershipType.CONSIGNMENT
    )
    in_stock = await client.get(
        "/api/v1/serialized-items", params={"status": "IN_STOCK"}, headers=_auth(store_id)
    )
    assert in_stock.status_code == 200
    codes = {row["item_code"] for row in in_stock.json()}
    assert codes == {"ITM-IN", "ITM-CONS"}
    consignment = await client.get(
        "/api/v1/serialized-items",
        params={"ownership": "CONSIGNMENT"},  # docs/04 合約參數名為 ownership
        headers=_auth(store_id),
    )
    assert {row["item_code"] for row in consignment.json()} == {"ITM-CONS"}


async def test_list_serialized_pagination(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(db_session)
    for i in range(5):
        await _seed_item(db_session, store_id, item_code=f"ITM-{i:04d}")
    page = await client.get(
        "/api/v1/serialized-items", params={"limit": 2, "offset": 2}, headers=_auth(store_id)
    )
    assert page.status_code == 200
    assert len(page.json()) == 2


async def test_list_catalog_products(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    store_id = await _seed_store(db_session)
    other = await _seed_store(db_session, "他店")
    db_session.add(
        CatalogProduct(
            store_id=store_id,
            sku="GAS-230",
            name="高山瓦斯罐 230g",
            unit_price=Decimal("120"),
            quantity_on_hand=37,
        )
    )
    db_session.add(
        CatalogProduct(store_id=other, sku="X", name="他店商品", unit_price=Decimal("1"))
    )
    await db_session.flush()
    resp = await client.get("/api/v1/catalog-products", headers=_auth(store_id))
    assert resp.status_code == 200
    rows = resp.json()
    assert [row["sku"] for row in rows] == ["GAS-230"]  # 只見本店（§4）
    assert rows[0]["unit_price"] == "120"
    assert rows[0]["quantity_on_hand"] == 37


async def test_list_bulk_lots_with_status_filter(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(db_session)

    def _lot(code: str, status: BulkLotStatus, remaining: int) -> BulkLot:
        return BulkLot(
            store_id=store_id,
            lot_code=code,
            name="營釘雜項",
            label="A堆",
            grade=Grade.E,
            acquisition_cost=Decimal("500"),
            acquisition_basis=BulkAcquisitionBasis.BAG,
            unit_price=Decimal("30"),
            total_qty=50,
            remaining_qty=remaining,
            status=status,
        )

    db_session.add(_lot("LOT-A", BulkLotStatus.ON_SALE, 12))
    db_session.add(_lot("LOT-B", BulkLotStatus.SOLD_OUT, 0))
    await db_session.flush()
    on_sale = await client.get(
        "/api/v1/bulk-lots", params={"status": "ON_SALE"}, headers=_auth(store_id)
    )
    assert on_sale.status_code == 200
    rows = on_sale.json()
    assert [row["lot_code"] for row in rows] == ["LOT-A"]
    assert rows[0]["unit_price"] == "30"
    assert rows[0]["acquisition_cost"] == "500"  # 散裝堆成本要顯示（docs/10 §5 /inventory）
    assert rows[0]["remaining_qty"] == 12
    everything = await client.get("/api/v1/bulk-lots", headers=_auth(store_id))
    assert {row["lot_code"] for row in everything.json()} == {"LOT-A", "LOT-B"}


async def test_list_serialized_q_searches_name_and_code(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """docs/04：/serialized-items?q= 搜尋品名或識別碼（不分大小寫、子字串）。"""
    store_id = await _seed_store(db_session)
    await _seed_item(db_session, store_id, item_code="ITM-0001")  # name=雙人帳篷
    sleeping = SerializedItem(
        store_id=store_id,
        item_code="ITM-0002",
        name="睡袋",
        grade=Grade.B,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal("300"),
        status=SerializedItemStatus.IN_STOCK,
    )
    db_session.add(sleeping)
    await db_session.flush()
    by_name = await client.get(
        "/api/v1/serialized-items", params={"q": "帳篷"}, headers=_auth(store_id)
    )
    assert {row["item_code"] for row in by_name.json()} == {"ITM-0001"}
    by_code = await client.get(
        "/api/v1/serialized-items", params={"q": "itm-0002"}, headers=_auth(store_id)
    )
    assert {row["item_code"] for row in by_code.json()} == {"ITM-0002"}


async def test_list_catalog_q_and_low_stock(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """docs/04：/catalog-products?q=&low_stock=；low_stock=true 篩 量≤再訂購點。"""
    store_id = await _seed_store(db_session)
    db_session.add(
        CatalogProduct(
            store_id=store_id,
            sku="GAS-230",
            name="高山瓦斯罐 230g",
            unit_price=Decimal("120"),
            quantity_on_hand=3,
            reorder_point=5,
        )
    )
    db_session.add(
        CatalogProduct(
            store_id=store_id,
            sku="ROPE-10",
            name="營繩 10m",
            unit_price=Decimal("80"),
            quantity_on_hand=40,
            reorder_point=5,
        )
    )
    await db_session.flush()
    low = await client.get(
        "/api/v1/catalog-products", params={"low_stock": "true"}, headers=_auth(store_id)
    )
    assert [row["sku"] for row in low.json()] == ["GAS-230"]
    by_q = await client.get(
        "/api/v1/catalog-products", params={"q": "rope"}, headers=_auth(store_id)
    )
    assert [row["sku"] for row in by_q.json()] == ["ROPE-10"]


async def test_list_bulk_lots_q_searches_name_label_code(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """docs/04：/bulk-lots?q= 搜尋名稱/堆名/識別碼。"""
    store_id = await _seed_store(db_session)

    def _lot(code: str, name: str, label: str | None) -> BulkLot:
        return BulkLot(
            store_id=store_id,
            lot_code=code,
            name=name,
            label=label,
            grade=Grade.E,
            acquisition_cost=Decimal("500"),
            acquisition_basis=BulkAcquisitionBasis.BAG,
            unit_price=Decimal("30"),
            total_qty=50,
            remaining_qty=10,
            status=BulkLotStatus.ON_SALE,
        )

    db_session.add(_lot("LOT-A1", "營釘雜項", "A堆"))
    db_session.add(_lot("LOT-B2", "餐具雜項", "B堆"))
    await db_session.flush()
    by_label = await client.get("/api/v1/bulk-lots", params={"q": "A堆"}, headers=_auth(store_id))
    assert [row["lot_code"] for row in by_label.json()] == ["LOT-A1"]
    by_name = await client.get("/api/v1/bulk-lots", params={"q": "餐具"}, headers=_auth(store_id))
    assert [row["lot_code"] for row in by_name.json()] == ["LOT-B2"]


async def test_get_bulk_lot_by_code(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """掃堆標籤（docs/04 GET /bulk-lots/by-code/{lot_code}；T18 標籤即印 lot_code Code128）。"""
    store_id = await _seed_store(db_session)
    other = await _seed_store(db_session, "他店")
    db_session.add(
        BulkLot(
            store_id=store_id,
            lot_code="LOT-0001",
            name="營釘雜項",
            grade=Grade.E,
            acquisition_cost=Decimal("500"),
            acquisition_basis=BulkAcquisitionBasis.BAG,
            unit_price=Decimal("30"),
            total_qty=50,
            remaining_qty=12,
            status=BulkLotStatus.ON_SALE,
        )
    )
    await db_session.flush()
    resp = await client.get("/api/v1/bulk-lots/by-code/LOT-0001", headers=_auth(store_id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["lot_code"] == "LOT-0001"
    assert body["unit_price"] == "30"
    assert body["remaining_qty"] == 12
    # 他店 token 查 → 404（§4）
    cross = await client.get("/api/v1/bulk-lots/by-code/LOT-0001", headers=_auth(other))
    assert cross.status_code == 404
    unknown = await client.get("/api/v1/bulk-lots/by-code/NOPE", headers=_auth(store_id))
    assert unknown.status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/serialized-items/by-code/ITM-0001",
        "/api/v1/serialized-items",
        "/api/v1/catalog-products",
        "/api/v1/bulk-lots",
        "/api/v1/bulk-lots/by-code/LOT-0001",
    ],
)
async def test_endpoints_require_auth(client: httpx.AsyncClient, path: str) -> None:
    resp = await client.get(path)
    assert resp.status_code == 401
