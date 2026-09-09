"""庫存頁序號品篩選：型號／成色條件，以及「這個品牌實際有什麼」的選項來源。

店員在庫存頁選了品牌之後，型號／分類／成色的下拉只該列出**該品牌真的有貨的**選項，
否則選出來是空清單。沒選品牌時則列出本店序號品實際用到的全部值。

唯讀查詢；金額不涉入；以 token 的 store_id 過濾（§4）。
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
from app.modules.inventory.models import Brand, Category, ProductModel, SerializedItem
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import Grade, OwnershipType, SerializedItemStatus, UserRole

OPTIONS = "/api/v1/serialized-items/filter-options"
LIST = "/api/v1/serialized-items"


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


async def _model(session: AsyncSession, store_id: int, brand: Brand, name: str) -> ProductModel:
    row = ProductModel(store_id=store_id, brand_id=brand.id, name=name)
    session.add(row)
    await session.flush()
    return row


async def _category(session: AsyncSession, store_id: int, name: str) -> Category:
    row = Category(store_id=store_id, name=name, target_margin_pct=45)
    session.add(row)
    await session.flush()
    return row


async def _item(
    session: AsyncSession,
    store_id: int,
    *,
    brand: Brand | None = None,
    model: ProductModel | None = None,
    category: Category | None = None,
    grade: Grade = Grade.A,
    status: SerializedItemStatus = SerializedItemStatus.IN_STOCK,
) -> SerializedItem:
    item = SerializedItem(
        store_id=store_id,
        item_code=f"ITM-{next(_SEQ):06d}",
        name="測試品",
        grade=grade,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal("100"),
        status=status,
        brand_id=None if brand is None else brand.id,
        product_model_id=None if model is None else model.id,
        category_id=None if category is None else category.id,
    )
    session.add(item)
    await session.flush()
    return item


async def test_options_narrow_to_the_selected_brand(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """選了品牌，型號／分類／成色只列該品牌真的有貨的——否則選出來是空清單。"""
    store_id = await _seed_store(db_session)
    bull = await _brand(db_session, store_id, "蠻牛")
    other = await _brand(db_session, store_id, "別牌")
    peg = await _model(db_session, store_id, bull, "營釘")
    pole = await _model(db_session, store_id, other, "營柱")
    gear = await _category(db_session, store_id, "配件")
    tent = await _category(db_session, store_id, "帳篷")
    await _item(db_session, store_id, brand=bull, model=peg, category=gear, grade=Grade.A)
    await _item(db_session, store_id, brand=other, model=pole, category=tent, grade=Grade.D)

    body = (
        await client.get(OPTIONS, params={"brand_id": bull.id}, headers=_auth(store_id))
    ).json()
    assert [m["name"] for m in body["models"]] == ["營釘"]
    assert [c["name"] for c in body["categories"]] == ["配件"]
    assert body["grades"] == ["A"]
    # 品牌一律列全部實際有貨的，否則選了之後就換不掉了
    assert {b["name"] for b in body["brands"]} == {"蠻牛", "別牌"}


async def test_options_without_brand_list_everything_in_stock(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """沒選品牌就全部可選。"""
    store_id = await _seed_store(db_session)
    bull = await _brand(db_session, store_id, "蠻牛")
    other = await _brand(db_session, store_id, "別牌")
    peg = await _model(db_session, store_id, bull, "營釘")
    pole = await _model(db_session, store_id, other, "營柱")
    gear = await _category(db_session, store_id, "配件")
    tent = await _category(db_session, store_id, "帳篷")
    await _item(db_session, store_id, brand=bull, model=peg, category=gear, grade=Grade.A)
    await _item(db_session, store_id, brand=other, model=pole, category=tent, grade=Grade.D)

    body = (await client.get(OPTIONS, headers=_auth(store_id))).json()
    assert {m["name"] for m in body["models"]} == {"營釘", "營柱"}
    assert {c["name"] for c in body["categories"]} == {"配件", "帳篷"}
    assert body["grades"] == ["A", "D"]


async def test_options_only_list_values_actually_used(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """建了但沒有任何庫存用到的品牌／型號／分類不該出現——那些選了也是空清單。"""
    store_id = await _seed_store(db_session)
    bull = await _brand(db_session, store_id, "蠻牛")
    await _brand(db_session, store_id, "從沒收過的牌子")
    peg = await _model(db_session, store_id, bull, "營釘")
    await _model(db_session, store_id, bull, "從沒收過的型號")
    gear = await _category(db_session, store_id, "配件")
    await _category(db_session, store_id, "從沒用過的分類")
    await _item(db_session, store_id, brand=bull, model=peg, category=gear)

    body = (await client.get(OPTIONS, headers=_auth(store_id))).json()
    assert [b["name"] for b in body["brands"]] == ["蠻牛"]
    assert [m["name"] for m in body["models"]] == ["營釘"]
    assert [c["name"] for c in body["categories"]] == ["配件"]


async def test_options_grades_sorted_best_first(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """成色由好到差，不是資料庫回傳的隨機順序。"""
    store_id = await _seed_store(db_session)
    bull = await _brand(db_session, store_id, "蠻牛")
    for grade in (Grade.C, Grade.S, Grade.B):
        await _item(db_session, store_id, brand=bull, grade=grade)

    body = (await client.get(OPTIONS, params={"brand_id": bull.id}, headers=_auth(store_id))).json()
    assert body["grades"] == ["S", "B", "C"]


async def test_options_are_store_scoped(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """他店的品牌／型號不得外洩（§4）。"""
    store_a = await _seed_store(db_session, "A 店")
    store_b = await _seed_store(db_session, "B 店")
    bull = await _brand(db_session, store_a, "蠻牛")
    peg = await _model(db_session, store_a, bull, "營釘")
    await _item(db_session, store_a, brand=bull, model=peg)

    body = (await client.get(OPTIONS, headers=_auth(store_b))).json()
    assert body["brands"] == []
    assert body["models"] == []
    assert body["grades"] == []


async def test_options_do_not_leak_other_stores_names_via_bad_references(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """就算有一筆商品指到他店的品牌／型號／分類，也不得把他店名稱列進下拉。

    正常資料不會這樣，但店別若只靠「商品必然指向本店」間接達成，一筆錯誤資料就會
    洩漏他店名稱。這道測試守著 repository 對品牌／型號／分類各自明示的 store 條件。
    """
    store_a = await _seed_store(db_session, "A 店")
    store_b = await _seed_store(db_session, "B 店")
    a_brand = await _brand(db_session, store_a, "他店品牌")
    a_model = await _model(db_session, store_a, a_brand, "他店型號")
    a_category = await _category(db_session, store_a, "他店分類")
    # B 店的商品，卻指到 A 店的品牌／型號／分類（刻意造出的錯誤資料）
    await _item(db_session, store_b, brand=a_brand, model=a_model, category=a_category)

    body = (await client.get(OPTIONS, headers=_auth(store_b))).json()
    assert body["brands"] == []
    assert body["models"] == []
    assert body["categories"] == []
    # 成色來自商品本身（本來就是 B 店的），所以仍會列出
    assert body["grades"] == ["A"]


async def test_list_can_filter_by_product_model(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """庫存清單要能只看某個型號。"""
    store_id = await _seed_store(db_session)
    bull = await _brand(db_session, store_id, "蠻牛")
    peg = await _model(db_session, store_id, bull, "營釘")
    pole = await _model(db_session, store_id, bull, "營柱")
    wanted = await _item(db_session, store_id, brand=bull, model=peg)
    await _item(db_session, store_id, brand=bull, model=pole)

    rows = (
        await client.get(
            LIST, params={"product_model_id": peg.id}, headers=_auth(store_id)
        )
    ).json()
    assert [r["item_code"] for r in rows] == [wanted.item_code]


async def test_list_can_filter_by_grade(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """庫存清單要能只看某個成色。"""
    store_id = await _seed_store(db_session)
    bull = await _brand(db_session, store_id, "蠻牛")
    wanted = await _item(db_session, store_id, brand=bull, grade=Grade.B)
    await _item(db_session, store_id, brand=bull, grade=Grade.D)

    rows = (await client.get(LIST, params={"grade": "B"}, headers=_auth(store_id))).json()
    assert [r["item_code"] for r in rows] == [wanted.item_code]


async def test_options_require_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get(OPTIONS)).status_code == 401
