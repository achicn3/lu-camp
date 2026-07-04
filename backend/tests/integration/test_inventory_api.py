"""inventory 唯讀查詢 API 整合測試（T19-pre-B）。

POS/庫存頁前置：掃碼查件（by-code）、序號品/數量品/散裝堆列表。
全部需認證、以 token 的 store_id 範圍過濾（§4）；金額一律字串整數元（§6/§11）。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.inventory.models import (
    Brand,
    BulkLot,
    CatalogProduct,
    Category,
    SerializedItem,
    StockMovement,
)
from app.modules.purchasing.models import PurchaseOrder, PurchaseOrderLine, Supplier
from app.modules.sales.models import Sale, SaleLine
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    Grade,
    ItemKind,
    OwnershipType,
    SaleInvoiceStatus,
    SaleLineType,
    SerializedItemStatus,
    StockDirection,
    StockReason,
    UserRole,
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


# store_id -> 該店一名 CLERK 的 user_id：認證已回 DB 覆核（D-4），token 的 user 必須真實存在。
_STORE_CLERKS: dict[int, int] = {}


async def _seed_store(session: AsyncSession, name: str = "測試門市") -> int:
    store = Store(name=name)
    session.add(store)
    await session.flush()
    clerk = User(
        store_id=store.id,
        username=f"clk-{store.id}",
        password_hash="h",
        role=UserRole.CLERK,
        is_active=True,
    )
    session.add(clerk)
    await session.flush()
    _STORE_CLERKS[store.id] = clerk.id
    return store.id


def _auth(store_id: int) -> dict[str, str]:
    token = encode_access_token(user_id=_STORE_CLERKS[store_id], role="CLERK", store_id=store_id)
    return {"Authorization": f"Bearer {token}"}


async def _auth_manager(session: AsyncSession, store_id: int) -> dict[str, str]:
    mgr = User(
        store_id=store_id,
        username=f"mgr-{store_id}",
        password_hash="h",
        role=UserRole.MANAGER,
        is_active=True,
    )
    session.add(mgr)
    await session.flush()
    token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store_id)
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


async def test_get_catalog_by_sku(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """POS 掃碼查數量品：以 SKU 取件（店範圍；他店/不存在一律 404）。"""
    store_id = await _seed_store(db_session)
    other = await _seed_store(db_session, "他店")
    db_session.add(
        CatalogProduct(
            store_id=store_id,
            sku="GAS-230",
            name="高山瓦斯罐 230g",
            unit_price=Decimal("120"),
            quantity_on_hand=5,
        )
    )
    await db_session.flush()

    resp = await client.get("/api/v1/catalog-products/by-sku/GAS-230", headers=_auth(store_id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["sku"] == "GAS-230"
    assert body["unit_price"] == "120"
    assert body["quantity_on_hand"] == 5

    # 他店同 SKU 不可見（§4）；不存在 → 404。
    cross = await client.get("/api/v1/catalog-products/by-sku/GAS-230", headers=_auth(other))
    assert cross.status_code == 404
    missing = await client.get("/api/v1/catalog-products/by-sku/NOPE", headers=_auth(store_id))
    assert missing.status_code == 404


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


async def test_create_catalog_product_then_listed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """上架數量型商品（MANAGER）：建檔後初始庫存 0，且出現在清單。"""
    store_id = await _seed_store(db_session)
    mgr = await _auth_manager(db_session, store_id)
    resp = await client.post(
        "/api/v1/catalog-products",
        json={
            "sku": "GAS-230",
            "name": "高山瓦斯罐 230g",
            "unit_price": "180",
            "reorder_point": 12,
        },
        headers=mgr,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sku"] == "GAS-230"
    assert body["quantity_on_hand"] == 0  # 補庫存一律走採購收貨
    assert body["reorder_point"] == 12
    # 出現在清單
    listed = await client.get("/api/v1/catalog-products", headers=_auth(store_id))
    assert any(p["sku"] == "GAS-230" for p in listed.json())


async def test_create_catalog_product_duplicate_sku_409(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(db_session)
    mgr = await _auth_manager(db_session, store_id)
    payload = {"sku": "DUP-1", "name": "重複品", "unit_price": "100"}
    first = await client.post("/api/v1/catalog-products", json=payload, headers=mgr)
    assert first.status_code == 201
    dup = await client.post("/api/v1/catalog-products", json=payload, headers=mgr)
    assert dup.status_code == 409


async def test_create_catalog_product_clerk_forbidden(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(db_session)
    resp = await client.post(
        "/api/v1/catalog-products",
        json={"sku": "X-1", "name": "店員不可上架", "unit_price": "100"},
        headers=_auth(store_id),
    )
    assert resp.status_code == 403


async def test_list_serialized_filters_category_and_brand(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """序號品清單可依品牌、類型篩選。"""
    store_id = await _seed_store(db_session)
    brand_a = Brand(store_id=store_id, name="Snow Peak")
    brand_b = Brand(store_id=store_id, name="Coleman")
    cat_x = Category(store_id=store_id, name="帳篷", target_margin_pct=40)
    cat_y = Category(store_id=store_id, name="燈具", target_margin_pct=40)
    db_session.add_all([brand_a, brand_b, cat_x, cat_y])
    await db_session.flush()
    i1 = SerializedItem(
        store_id=store_id, item_code="F-1", name="帳A", grade=Grade.A,
        ownership_type=OwnershipType.OWNED, listed_price=Decimal(1000),
        brand_id=brand_a.id, category_id=cat_x.id,
    )
    i2 = SerializedItem(
        store_id=store_id, item_code="F-2", name="燈B", grade=Grade.A,
        ownership_type=OwnershipType.OWNED, listed_price=Decimal(800),
        brand_id=brand_b.id, category_id=cat_y.id,
    )
    db_session.add_all([i1, i2])
    await db_session.flush()

    by_brand = await client.get(
        "/api/v1/serialized-items", params={"brand_id": brand_a.id}, headers=_auth(store_id)
    )
    assert [r["item_code"] for r in by_brand.json()] == ["F-1"]
    by_cat = await client.get(
        "/api/v1/serialized-items", params={"category_id": cat_y.id}, headers=_auth(store_id)
    )
    assert [r["item_code"] for r in by_cat.json()] == ["F-2"]


async def test_list_serialized_aging_min_age_and_oldest_first(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """久滯庫存：min_age_days 撈入庫≥N 天、oldest_first 以入庫最久排序。"""
    store_id = await _seed_store(db_session)
    now = datetime.now(UTC)
    old = SerializedItem(
        store_id=store_id, item_code="AGE-OLD", name="久滯帳篷", grade=Grade.A,
        ownership_type=OwnershipType.OWNED, listed_price=Decimal(1000),
        intake_date=now - timedelta(days=120),
    )
    recent = SerializedItem(
        store_id=store_id, item_code="AGE-NEW", name="新進帳篷", grade=Grade.A,
        ownership_type=OwnershipType.OWNED, listed_price=Decimal(1000),
        intake_date=now - timedelta(days=5),
    )
    db_session.add_all([old, recent])
    await db_session.flush()

    aged = await client.get(
        "/api/v1/serialized-items",
        params={"min_age_days": 90, "oldest_first": "true"},
        headers=_auth(store_id),
    )
    assert [r["item_code"] for r in aged.json()] == ["AGE-OLD"]

    both = await client.get(
        "/api/v1/serialized-items",
        params={"min_age_days": 1, "oldest_first": "true"},
        headers=_auth(store_id),
    )
    assert [r["item_code"] for r in both.json()] == ["AGE-OLD", "AGE-NEW"]


async def test_aging_query_excludes_non_in_stock_items(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """久滯庫存語意僅含在庫品：min_age_days 未帶 status 時，已售/已沖銷的老件不得回傳；
    明確帶 status 仍可查（不破壞既有篩選）。"""
    store_id = await _seed_store(db_session)
    now = datetime.now(UTC)
    sold_old = SerializedItem(
        store_id=store_id, item_code="AGE-SOLD", name="已售老帳篷", grade=Grade.A,
        ownership_type=OwnershipType.OWNED, listed_price=Decimal(1000),
        intake_date=now - timedelta(days=200),
        status=SerializedItemStatus.SOLD, sold_date=now - timedelta(days=10),
    )
    written_off_old = SerializedItem(
        store_id=store_id, item_code="AGE-WO", name="已沖銷老帳篷", grade=Grade.A,
        ownership_type=OwnershipType.OWNED, listed_price=Decimal(1000),
        intake_date=now - timedelta(days=200),
        status=SerializedItemStatus.WRITTEN_OFF,
    )
    stocked_old = SerializedItem(
        store_id=store_id, item_code="AGE-IN", name="在庫老帳篷", grade=Grade.A,
        ownership_type=OwnershipType.OWNED, listed_price=Decimal(1000),
        intake_date=now - timedelta(days=200),
    )
    db_session.add_all([sold_old, written_off_old, stocked_old])
    await db_session.flush()

    # 未帶 status：只回在庫老件，排除 SOLD / WRITTEN_OFF。
    aged = await client.get(
        "/api/v1/serialized-items",
        params={"min_age_days": 90, "oldest_first": "true"},
        headers=_auth(store_id),
    )
    assert [r["item_code"] for r in aged.json()] == ["AGE-IN"]

    # 明確帶 status 仍尊重該篩選（可查老的已售件）。
    sold = await client.get(
        "/api/v1/serialized-items",
        params={"min_age_days": 90, "oldest_first": "true", "status": "SOLD"},
        headers=_auth(store_id),
    )
    assert [r["item_code"] for r in sold.json()] == ["AGE-SOLD"]


async def test_serialized_detail_aggregates_source_sale_and_history(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """逐件明細：來源（寄售人）、售價/成交價、售出單號、完整異動歷史。"""
    store_id = await _seed_store(db_session)
    consignor = Contact(
        store_id=store_id, name="寄售人甲", phone="0911222333", roles=["CONSIGNOR"]
    )
    db_session.add(consignor)
    await db_session.flush()
    item = SerializedItem(
        store_id=store_id, item_code="DET-1", name="寄售帳篷", grade=Grade.A,
        ownership_type=OwnershipType.CONSIGNMENT, listed_price=Decimal(2000),
        consignor_id=consignor.id, commission_pct=50,
        status=SerializedItemStatus.SOLD, sold_date=datetime.now(UTC),
    )
    db_session.add(item)
    await db_session.flush()
    clerk_id = _STORE_CLERKS[store_id]
    sale = Sale(
        store_id=store_id, clerk_user_id=clerk_id,
        subtotal=Decimal(1900), tax=Decimal(100), total=Decimal(2000),
    )
    db_session.add(sale)
    await db_session.flush()
    line = SaleLine(
        store_id=store_id, sale_id=sale.id, line_type=SaleLineType.SERIALIZED,
        serialized_item_id=item.id, description="寄售帳篷", qty=1,
        unit_price=Decimal(2000), line_total=Decimal(2000),
    )
    mv_in = StockMovement(
        store_id=store_id, item_kind=ItemKind.SERIALIZED, serialized_item_id=item.id,
        direction=StockDirection.IN, qty=1, reason=StockReason.ACQUISITION,
        ref_type="acquisition", ref_id=1,
    )
    mv_out = StockMovement(
        store_id=store_id, item_kind=ItemKind.SERIALIZED, serialized_item_id=item.id,
        direction=StockDirection.OUT, qty=1, reason=StockReason.SALE,
        ref_type="sale", ref_id=sale.id,
    )
    db_session.add_all([line, mv_in, mv_out])
    await db_session.flush()

    mgr = await _auth_manager(db_session, store_id)
    resp = await client.get(f"/api/v1/serialized-items/{item.id}/detail", headers=mgr)
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["source"]["name"] == "寄售人甲"
    assert d["source"]["phone"] == "0911222333"
    assert d["source"]["kind"] == "CONSIGNOR"
    assert d["sold_price"] == "2000"
    assert d["sale_id"] == sale.id
    events = [e["event"] for e in d["history"]]
    assert events == ["入庫（收購）", "售出"]


async def test_serialized_detail_ignores_voided_sale(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已作廢銷售不得成為在庫品的成交資料（Codex P2）：sale_id/sold_price 為空。"""
    store_id = await _seed_store(db_session)
    item = await _seed_item(db_session, store_id, item_code="DET-VOID")  # IN_STOCK（作廢已回補）
    clerk_id = _STORE_CLERKS[store_id]
    sale = Sale(
        store_id=store_id, clerk_user_id=clerk_id,
        subtotal=Decimal(1900), tax=Decimal(100), total=Decimal(2000),
        invoice_status=SaleInvoiceStatus.VOID,
    )
    db_session.add(sale)
    await db_session.flush()
    db_session.add(
        SaleLine(
            store_id=store_id, sale_id=sale.id, line_type=SaleLineType.SERIALIZED,
            serialized_item_id=item.id, description="雙人帳篷", qty=1,
            unit_price=Decimal(2000), line_total=Decimal(2000),
        )
    )
    await db_session.flush()

    mgr = await _auth_manager(db_session, store_id)
    resp = await client.get(f"/api/v1/serialized-items/{item.id}/detail", headers=mgr)
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["sale_id"] is None
    assert d["sold_price"] is None


async def test_serialized_detail_ignores_returned_sale(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已退貨（非作廢、已回補 IN_STOCK）的在庫品，不得顯示已退款的成交/獲利（Codex P2）。"""
    store_id = await _seed_store(db_session)
    # 退貨後品項回補為 IN_STOCK，但原（非作廢）sale_line 仍在。
    item = SerializedItem(
        store_id=store_id, item_code="DET-RET", name="退貨帳篷", grade=Grade.A,
        ownership_type=OwnershipType.OWNED, listed_price=Decimal(2000),
        acquisition_cost=Decimal(1200), status=SerializedItemStatus.IN_STOCK,
    )
    db_session.add(item)
    await db_session.flush()
    clerk_id = _STORE_CLERKS[store_id]
    sale = Sale(
        store_id=store_id, clerk_user_id=clerk_id,
        subtotal=Decimal(1900), tax=Decimal(100), total=Decimal(2000),
    )
    db_session.add(sale)
    await db_session.flush()
    db_session.add(
        SaleLine(
            store_id=store_id, sale_id=sale.id, line_type=SaleLineType.SERIALIZED,
            serialized_item_id=item.id, description="退貨帳篷", qty=1,
            unit_price=Decimal(2000), line_total=Decimal(2000),
        )
    )
    await db_session.flush()

    mgr = await _auth_manager(db_session, store_id)
    resp = await client.get(f"/api/v1/serialized-items/{item.id}/detail", headers=mgr)
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["sale_id"] is None
    assert d["sold_price"] is None
    assert d["margin"] is None  # 不得顯示已退款的獲利


async def test_serialized_detail_unknown_returns_404(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(db_session)
    mgr = await _auth_manager(db_session, store_id)
    resp = await client.get("/api/v1/serialized-items/999999/detail", headers=mgr)
    assert resp.status_code == 404


async def test_catalog_detail_shows_supplier_purchase_history(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """數量品明細：經銷商進貨歷史（供應商/數量/單價）＋異動歷史。"""
    store_id = await _seed_store(db_session)
    clerk_id = _STORE_CLERKS[store_id]
    supplier = Supplier(store_id=store_id, name="山野貿易")
    product = CatalogProduct(
        store_id=store_id, sku="GAS-230", name="高山瓦斯罐 230g",
        unit_price=Decimal(120), quantity_on_hand=10,
    )
    db_session.add_all([supplier, product])
    await db_session.flush()
    po = PurchaseOrder(store_id=store_id, supplier_id=supplier.id, ordered_by=clerk_id)
    db_session.add(po)
    await db_session.flush()
    db_session.add_all([
        PurchaseOrderLine(
            store_id=store_id, purchase_order_id=po.id, catalog_product_id=product.id,
            qty=10, unit_cost=Decimal(60),
        ),
        StockMovement(
            store_id=store_id, item_kind=ItemKind.CATALOG, catalog_product_id=product.id,
            direction=StockDirection.IN, qty=10, reason=StockReason.PURCHASE,
            ref_type="purchase_order", ref_id=po.id,
        ),
    ])
    await db_session.flush()

    mgr = await _auth_manager(db_session, store_id)
    resp = await client.get(
        f"/api/v1/catalog-products/{product.id}/detail", headers=mgr
    )
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["sku"] == "GAS-230"
    assert len(d["purchases"]) == 1
    assert d["purchases"][0]["supplier_name"] == "山野貿易"
    assert d["purchases"][0]["unit_cost"] == "60"
    assert [h["event"] for h in d["history"]] == ["入庫（進貨）"]


async def test_bulk_detail_shows_source_and_cost(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """散裝批明細：來源（寄售人）、收購成本、均一價、剩餘、異動歷史。"""
    store_id = await _seed_store(db_session)
    consignor = Contact(
        store_id=store_id, name="散裝寄售人", phone="0922333444", roles=["CONSIGNOR"]
    )
    db_session.add(consignor)
    await db_session.flush()
    lot = BulkLot(
        store_id=store_id, lot_code="LOT-1", name="營釘雜項", grade=Grade.E,
        consignor_id=consignor.id, acquisition_cost=Decimal(500),
        acquisition_basis=BulkAcquisitionBasis.BAG, unit_price=Decimal(50),
        total_qty=10, remaining_qty=7, status=BulkLotStatus.ON_SALE,
    )
    db_session.add(lot)
    await db_session.flush()
    db_session.add(
        StockMovement(
            store_id=store_id, item_kind=ItemKind.BULK_LOT, bulk_lot_id=lot.id,
            direction=StockDirection.IN, qty=10, reason=StockReason.ACQUISITION,
            ref_type="acquisition", ref_id=1,
        )
    )
    await db_session.flush()

    mgr = await _auth_manager(db_session, store_id)
    resp = await client.get(f"/api/v1/bulk-lots/{lot.id}/detail", headers=mgr)
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["source"]["name"] == "散裝寄售人"
    assert d["source"]["kind"] == "CONSIGNOR"
    assert d["acquisition_cost"] == "500"
    assert d["remaining_qty"] == 7
    assert [h["event"] for h in d["history"]] == ["入庫（收購）"]


async def test_serialized_detail_forbidden_for_clerk(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """逐件明細含收購成本/毛利（敏感）→ 限管理者；CLERK 取得 403（Codex P1）。"""
    store_id = await _seed_store(db_session)
    item = await _seed_item(db_session, store_id, item_code="DET-CLERK")
    resp = await client.get(
        f"/api/v1/serialized-items/{item.id}/detail", headers=_auth(store_id)
    )
    assert resp.status_code == 403


async def test_update_serialized_price_manager_audits(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """改序號品標價（限管理者；在庫）：價更新並寫稽核 UPDATE_PRICE（before/after）。"""
    from app.core.audit import AuditLog

    store_id = await _seed_store(db_session)
    item = await _seed_item(db_session, store_id, item_code="PRICE-1", listed_price="1280")
    mgr = await _auth_manager(db_session, store_id)
    await db_session.commit()  # 釋出 savepoint，端點 commit/rollback 不影響種子

    resp = await client.patch(
        f"/api/v1/serialized-items/{item.id}/price",
        json={"unit_price": "1680"},
        headers=mgr,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["listed_price"] == "1680"  # 回應即更新後標價
    audits = (
        await db_session.scalars(
            select(AuditLog).where(
                AuditLog.action == "UPDATE_PRICE", AuditLog.store_id == store_id
            )
        )
    ).all()
    assert len(audits) == 1


async def test_update_serialized_price_forbidden_for_clerk(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(db_session)
    item = await _seed_item(db_session, store_id, item_code="PRICE-2")
    resp = await client.patch(
        f"/api/v1/serialized-items/{item.id}/price",
        json={"unit_price": "999"},
        headers=_auth(store_id),
    )
    assert resp.status_code == 403


async def test_update_serialized_price_rejects_sold(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """已售出不可改價 → 409。"""
    store_id = await _seed_store(db_session)
    sold = await _seed_item(
        db_session, store_id, item_code="PRICE-3", status=SerializedItemStatus.SOLD
    )
    mgr = await _auth_manager(db_session, store_id)
    await db_session.commit()
    r = await client.patch(
        f"/api/v1/serialized-items/{sold.id}/price", json={"unit_price": "100"}, headers=mgr
    )
    assert r.status_code == 409


async def test_update_serialized_price_rejects_zero(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """非正整數售價 → 422（schema 驗證，端點前擋下）。"""
    store_id = await _seed_store(db_session)
    item = await _seed_item(db_session, store_id, item_code="PRICE-4")
    mgr = await _auth_manager(db_session, store_id)
    await db_session.commit()
    r = await client.patch(
        f"/api/v1/serialized-items/{item.id}/price", json={"unit_price": "0"}, headers=mgr
    )
    assert r.status_code == 422


async def test_update_catalog_and_bulk_price(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(db_session)
    mgr = await _auth_manager(db_session, store_id)
    product = CatalogProduct(
        store_id=store_id, sku="CP-1", name="瓦斯罐", unit_price=Decimal(120), quantity_on_hand=5
    )
    lot = BulkLot(
        store_id=store_id, lot_code="LOT-P", name="營釘", grade=Grade.E,
        acquisition_cost=Decimal(300), acquisition_basis=BulkAcquisitionBasis.BAG,
        unit_price=Decimal(50), total_qty=10, remaining_qty=10, status=BulkLotStatus.ON_SALE,
    )
    db_session.add_all([product, lot])
    await db_session.flush()

    rc = await client.patch(
        f"/api/v1/catalog-products/{product.id}/price", json={"unit_price": "150"}, headers=mgr
    )
    assert rc.status_code == 200 and rc.json()["unit_price"] == "150"
    rb = await client.patch(
        f"/api/v1/bulk-lots/{lot.id}/price", json={"unit_price": "65"}, headers=mgr
    )
    assert rb.status_code == 200 and rb.json()["unit_price"] == "65"
