"""一般商品／散裝批：篩選條件、總筆數與「實際有什麼」的選項來源。

與序號品同一套做法（見 test_inventory_filter_options.py）：清單能依品牌等條件篩、
有對應的總筆數可算頁數、下拉只列實際有貨的值，選了品牌就把其餘選項收斂。

一般商品沒有型號／成色／分類欄位（catalog_products 只有 brand_id），所以它只有品牌
一個維度、沒有東西可收斂；散裝批有品牌／分類／成色三個維度。
"""

from collections.abc import AsyncGenerator
from decimal import Decimal
from itertools import count

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import Brand, BulkLot, CatalogProduct, Category
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import BulkAcquisitionBasis, BulkLotStatus, Grade, UserRole

CATALOG = "/api/v1/catalog-products"
CATALOG_COUNT = "/api/v1/catalog-products/count"
CATALOG_OPTIONS = "/api/v1/catalog-products/filter-options"
BULK = "/api/v1/bulk-lots"
BULK_COUNT = "/api/v1/bulk-lots/count"
BULK_OPTIONS = "/api/v1/bulk-lots/filter-options"


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


_STORE_CLERKS: dict[int, int] = {}
_SEQ = count(1)


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


async def _brand(session: AsyncSession, store_id: int, name: str) -> Brand:
    row = Brand(store_id=store_id, name=name)
    session.add(row)
    await session.flush()
    return row


async def _category(session: AsyncSession, store_id: int, name: str) -> Category:
    row = Category(store_id=store_id, name=name, target_margin_pct=45)
    session.add(row)
    await session.flush()
    return row


async def _catalog(
    session: AsyncSession, store_id: int, *, brand: Brand | None = None, name: str = "營釘"
) -> CatalogProduct:
    row = CatalogProduct(
        store_id=store_id,
        sku=f"SKU-{next(_SEQ):06d}",
        name=name,
        brand_id=None if brand is None else brand.id,
        unit_price=Decimal("100"),
        quantity_on_hand=5,
        reorder_point=1,
    )
    session.add(row)
    await session.flush()
    return row


async def _lot(
    session: AsyncSession,
    store_id: int,
    *,
    brand: Brand | None = None,
    category: Category | None = None,
    grade: Grade = Grade.E,
    status: BulkLotStatus = BulkLotStatus.ON_SALE,
    name: str = "雜物一堆",
) -> BulkLot:
    row = BulkLot(
        store_id=store_id,
        lot_code=f"LOT-{next(_SEQ):06d}",
        name=name,
        grade=grade,
        acquisition_cost=Decimal("500"),
        acquisition_basis=BulkAcquisitionBasis.UNSPECIFIED,
        unit_price=Decimal("50"),
        total_qty=10,
        remaining_qty=10,
        status=status,
        brand_id=None if brand is None else brand.id,
        category_id=None if category is None else category.id,
    )
    session.add(row)
    await session.flush()
    return row


# ── 一般商品 ───────────────────────────────────────────────────────────


async def test_catalog_can_filter_by_brand(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(db_session)
    bull = await _brand(db_session, store_id, "蠻牛")
    other = await _brand(db_session, store_id, "別牌")
    wanted = await _catalog(db_session, store_id, brand=bull)
    await _catalog(db_session, store_id, brand=other)

    rows = (
        await client.get(CATALOG, params={"brand_id": bull.id}, headers=_auth(store_id))
    ).json()
    assert [r["sku"] for r in rows] == [wanted.sku]


async def test_catalog_count_matches_the_filtered_list(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(db_session)
    bull = await _brand(db_session, store_id, "蠻牛")
    for _ in range(3):
        await _catalog(db_session, store_id, brand=bull)
    await _catalog(db_session, store_id, brand=None)

    assert (await client.get(CATALOG_COUNT, headers=_auth(store_id))).json()["count"] == 4
    assert (
        await client.get(CATALOG_COUNT, params={"brand_id": bull.id}, headers=_auth(store_id))
    ).json()["count"] == 3


async def test_catalog_count_honours_low_stock_and_search(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """總數必須與清單套用同一組條件，否則頁數會跟翻得到的頁對不起來。"""
    store_id = await _seed_store(db_session)
    await _catalog(db_session, store_id, name="一般品")
    low = await _catalog(db_session, store_id, name="快沒貨了")
    low.quantity_on_hand = 0
    await db_session.flush()

    assert (await client.get(CATALOG_COUNT, headers=_auth(store_id))).json()["count"] == 2
    assert (
        await client.get(CATALOG_COUNT, params={"low_stock": "true"}, headers=_auth(store_id))
    ).json()["count"] == 1
    assert (
        await client.get(CATALOG_COUNT, params={"q": "快沒貨"}, headers=_auth(store_id))
    ).json()["count"] == 1


async def test_catalog_options_only_list_brands_in_use(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """建了卻沒有一般商品掛著的品牌不列出來——選了也是空清單。"""
    store_id = await _seed_store(db_session)
    bull = await _brand(db_session, store_id, "蠻牛")
    await _brand(db_session, store_id, "沒有一般商品的牌子")
    await _catalog(db_session, store_id, brand=bull)

    body = (await client.get(CATALOG_OPTIONS, headers=_auth(store_id))).json()
    assert [b["name"] for b in body["brands"]] == ["蠻牛"]


async def test_catalog_options_are_store_scoped(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_a = await _seed_store(db_session, "A 店")
    store_b = await _seed_store(db_session, "B 店")
    bull = await _brand(db_session, store_a, "蠻牛")
    await _catalog(db_session, store_a, brand=bull)

    assert (await client.get(CATALOG_OPTIONS, headers=_auth(store_b))).json()["brands"] == []


# ── 散裝批 ─────────────────────────────────────────────────────────────


async def test_bulk_can_filter_by_brand_category_and_grade(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(db_session)
    bull = await _brand(db_session, store_id, "蠻牛")
    other = await _brand(db_session, store_id, "別牌")
    gear = await _category(db_session, store_id, "配件")
    tent = await _category(db_session, store_id, "帳篷")
    wanted = await _lot(db_session, store_id, brand=bull, category=gear, grade=Grade.E)
    await _lot(db_session, store_id, brand=other, category=tent, grade=Grade.D)

    for params in (
        {"brand_id": bull.id},
        {"category_id": gear.id},
        {"grade": "E"},
    ):
        rows = (await client.get(BULK, params=params, headers=_auth(store_id))).json()
        assert [r["lot_code"] for r in rows] == [wanted.lot_code], params


async def test_bulk_count_matches_the_filtered_list(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(db_session)
    bull = await _brand(db_session, store_id, "蠻牛")
    for _ in range(3):
        await _lot(db_session, store_id, brand=bull)
    await _lot(db_session, store_id, brand=None, status=BulkLotStatus.SOLD_OUT)

    assert (await client.get(BULK_COUNT, headers=_auth(store_id))).json()["count"] == 4
    assert (
        await client.get(BULK_COUNT, params={"brand_id": bull.id}, headers=_auth(store_id))
    ).json()["count"] == 3
    assert (
        await client.get(BULK_COUNT, params={"status": "ON_SALE"}, headers=_auth(store_id))
    ).json()["count"] == 3


async def test_bulk_options_narrow_to_the_selected_brand(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """選了品牌，分類與成色只列該品牌真的有的。"""
    store_id = await _seed_store(db_session)
    bull = await _brand(db_session, store_id, "蠻牛")
    other = await _brand(db_session, store_id, "別牌")
    gear = await _category(db_session, store_id, "配件")
    tent = await _category(db_session, store_id, "帳篷")
    await _lot(db_session, store_id, brand=bull, category=gear, grade=Grade.E)
    await _lot(db_session, store_id, brand=other, category=tent, grade=Grade.D)

    body = (
        await client.get(BULK_OPTIONS, params={"brand_id": bull.id}, headers=_auth(store_id))
    ).json()
    assert [c["name"] for c in body["categories"]] == ["配件"]
    assert body["grades"] == ["E"]
    # 品牌一律列全部實際有的，否則選了就換不掉
    assert {b["name"] for b in body["brands"]} == {"蠻牛", "別牌"}


async def test_bulk_options_without_brand_list_everything(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store_id = await _seed_store(db_session)
    bull = await _brand(db_session, store_id, "蠻牛")
    other = await _brand(db_session, store_id, "別牌")
    gear = await _category(db_session, store_id, "配件")
    tent = await _category(db_session, store_id, "帳篷")
    await _lot(db_session, store_id, brand=bull, category=gear, grade=Grade.D)
    await _lot(db_session, store_id, brand=other, category=tent, grade=Grade.E)

    body = (await client.get(BULK_OPTIONS, headers=_auth(store_id))).json()
    assert {c["name"] for c in body["categories"]} == {"配件", "帳篷"}
    assert body["grades"] == ["D", "E"]  # 由好到差


async def test_bulk_options_do_not_leak_other_stores_names(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同序號品：被 join 的品牌／分類各自也要擋 store_id（§4）。"""
    store_a = await _seed_store(db_session, "A 店")
    store_b = await _seed_store(db_session, "B 店")
    a_brand = await _brand(db_session, store_a, "他店品牌")
    a_category = await _category(db_session, store_a, "他店分類")
    await _lot(db_session, store_b, brand=a_brand, category=a_category)

    body = (await client.get(BULK_OPTIONS, headers=_auth(store_b))).json()
    assert body["brands"] == []
    assert body["categories"] == []
    assert body["grades"] == ["E"]  # 成色來自散裝批本身，本來就是 B 店的


async def test_counts_require_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get(CATALOG_COUNT)).status_code == 401
    assert (await client.get(BULK_COUNT)).status_code == 401
