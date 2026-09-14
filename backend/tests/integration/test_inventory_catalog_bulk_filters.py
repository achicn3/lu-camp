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
from app.modules.inventory.models import (
    Brand,
    BulkLot,
    CatalogProduct,
    Category,
    ProductModel,
)
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


async def test_create_catalog_accepts_brand_model_and_category(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """採購建品要能填品牌／型號／分類（2026-09-14 裁示：比照收購頁）。

    少了它們，同一款營繩在「收購來的二手」與「採購來的全新」之間，庫存篩選與標籤
    就對不起來。
    """
    store_id = await _seed_store(db_session, "採購建品店")
    brand = await _brand(db_session, store_id, "Snow Peak")
    category = await _category(db_session, store_id, "配件")
    model = ProductModel(store_id=store_id, brand_id=brand.id, name="營繩 4mm")
    db_session.add(model)
    await db_session.flush()

    resp = await client.post(
        CATALOG,
        json={
            "name": "營繩 4mm（全新）",
            "unit_price": "180",
            "brand_id": brand.id,
            "product_model_id": model.id,
            "category_id": category.id,
        },
        headers={**_auth(store_id), "Idempotency-Key": "catalog-brand-model"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["brand_id"] == brand.id
    assert body["product_model_id"] == model.id
    assert body["category_id"] == category.id


async def test_create_catalog_rejects_another_stores_model(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """§4：型號與分類同樣要限本店，不能借用別店的主檔。"""
    store_id = await _seed_store(db_session, "本店")
    other_store = await _seed_store(db_session, "別店")
    other_brand = await _brand(db_session, other_store, "別店品牌")
    other_model = ProductModel(store_id=other_store, brand_id=other_brand.id, name="別店型號")
    db_session.add(other_model)
    await db_session.flush()

    resp = await client.post(
        CATALOG,
        json={
            "name": "借用別店型號",
            "unit_price": "100",
            "product_model_id": other_model.id,
        },
        headers={**_auth(store_id), "Idempotency-Key": "catalog-cross-store"},
    )
    assert resp.status_code == 422


async def test_create_catalog_same_key_different_model_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同一個冪等鍵改了型號要被擋——否則重送會靜默沿用舊商品，店員以為改到了。"""
    store_id = await _seed_store(db_session, "冪等店")
    brand = await _brand(db_session, store_id, "品牌")
    first = ProductModel(store_id=store_id, brand_id=brand.id, name="型號一")
    second = ProductModel(store_id=store_id, brand_id=brand.id, name="型號二")
    db_session.add_all([first, second])
    await db_session.flush()
    headers = {**_auth(store_id), "Idempotency-Key": "catalog-same-key"}
    body = {"name": "同鍵商品", "unit_price": "100", "product_model_id": first.id}

    created = await client.post(CATALOG, json=body, headers=headers)
    assert created.status_code == 201, created.text
    replay = await client.post(CATALOG, json=body, headers=headers)
    assert replay.status_code in (200, 201)  # 同鍵同內容＝重播原商品
    assert replay.json()["id"] == created.json()["id"]

    changed = await client.post(
        CATALOG, json={**body, "product_model_id": second.id}, headers=headers
    )
    assert changed.status_code == 409
