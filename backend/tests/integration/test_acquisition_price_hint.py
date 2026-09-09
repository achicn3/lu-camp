"""收購定價提示（同款歷史行情）整合測試。

店員收購時，選了品牌＋型號就看得到「以前這款收多少、賣多少」，定價不必靠記憶。
唯讀查詢，不寫任何資料。金額一律字串整數元（§6/§11）、以 token 的 store_id 過濾（§4）。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import Brand, ProductModel, SerializedItem
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import Grade, OwnershipType, SerializedItemStatus, UserRole

PATH = "/api/v1/serialized-items/price-hint"


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


async def _seed_brand_model(
    session: AsyncSession, store_id: int, *, brand: str = "蠻牛", model: str = "營釘 20cm"
) -> tuple[int, int]:
    b = Brand(store_id=store_id, name=brand)
    session.add(b)
    await session.flush()
    m = ProductModel(store_id=store_id, brand_id=b.id, name=model)
    session.add(m)
    await session.flush()
    return b.id, m.id


async def _seed_item(
    session: AsyncSession,
    store_id: int,
    *,
    brand_id: int | None,
    product_model_id: int | None,
    listed_price: str,
    acquisition_cost: str | None = None,
    grade: Grade = Grade.A,
    status: SerializedItemStatus = SerializedItemStatus.IN_STOCK,
    ownership: OwnershipType = OwnershipType.OWNED,
    days_ago: int = 30,
    item_code: str | None = None,
) -> SerializedItem:
    item = SerializedItem(
        store_id=store_id,
        item_code=item_code or f"ITM-{next(_SEQ):06d}",
        name="營釘",
        grade=grade,
        ownership_type=ownership,
        listed_price=Decimal(listed_price),
        acquisition_cost=None if acquisition_cost is None else Decimal(acquisition_cost),
        status=status,
        brand_id=brand_id,
        product_model_id=product_model_id,
    )
    session.add(item)
    await session.flush()
    # created_at 由 server_default 帶入，測「近 12 個月」窗口要自己回填。
    item.created_at = datetime.now(UTC) - timedelta(days=days_ago)
    await session.flush()
    return item


async def test_reports_range_count_and_latest(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同品牌型號的歷史：收購價與上架售價各自的區間、筆數，以及最近一次的實際數字。"""
    store_id = await _seed_store(db_session)
    brand_id, model_id = await _seed_brand_model(db_session, store_id)
    await _seed_item(
        db_session,
        store_id,
        brand_id=brand_id,
        product_model_id=model_id,
        acquisition_cost="35",
        listed_price="100",
        days_ago=200,
    )
    await _seed_item(
        db_session,
        store_id,
        brand_id=brand_id,
        product_model_id=model_id,
        acquisition_cost="45",
        listed_price="130",
        days_ago=10,
    )

    resp = await client.get(
        PATH, params={"brand_id": brand_id, "product_model_id": model_id}, headers=_auth(store_id)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 2
    assert body["window_months"] == 12
    assert body["used_all_time"] is False

    assert len(body["grades"]) == 1
    a_grade = body["grades"][0]
    assert a_grade["grade"] == "A"
    assert a_grade["count"] == 2
    assert a_grade["cost_min"] == "35"
    assert a_grade["cost_max"] == "45"
    assert a_grade["listed_min"] == "100"
    assert a_grade["listed_max"] == "130"

    # 最近一次＝10 天前那件，讓店員看得到「上次就是這樣定的」
    assert body["latest"]["cost"] == "45"
    assert body["latest"]["listed_price"] == "130"
    assert body["latest"]["grade"] == "A"


async def test_groups_by_grade(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """成色是價差主因，必須分開列，不能混成一個區間。"""
    store_id = await _seed_store(db_session)
    brand_id, model_id = await _seed_brand_model(db_session, store_id)
    await _seed_item(
        db_session,
        store_id,
        brand_id=brand_id,
        product_model_id=model_id,
        acquisition_cost="45",
        listed_price="130",
        grade=Grade.A,
    )
    await _seed_item(
        db_session,
        store_id,
        brand_id=brand_id,
        product_model_id=model_id,
        acquisition_cost="20",
        listed_price="70",
        grade=Grade.C,
    )

    body = (
        await client.get(
            PATH,
            params={"brand_id": brand_id, "product_model_id": model_id},
            headers=_auth(store_id),
        )
    ).json()
    grades = {g["grade"]: g for g in body["grades"]}
    assert set(grades) == {"A", "C"}
    assert grades["A"]["listed_min"] == "130"
    assert grades["C"]["listed_max"] == "70"
    # S→D 由好到差排序，店員視線由上往下就是價格由高到低
    assert [g["grade"] for g in body["grades"]] == ["A", "C"]


async def test_excludes_voided_acquisitions(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """作廢收購的件（WRITTEN_OFF）不是成交行情，不得混進區間。"""
    store_id = await _seed_store(db_session)
    brand_id, model_id = await _seed_brand_model(db_session, store_id)
    await _seed_item(
        db_session,
        store_id,
        brand_id=brand_id,
        product_model_id=model_id,
        acquisition_cost="40",
        listed_price="120",
    )
    await _seed_item(
        db_session,
        store_id,
        brand_id=brand_id,
        product_model_id=model_id,
        acquisition_cost="999",
        listed_price="9999",
        status=SerializedItemStatus.WRITTEN_OFF,
    )

    body = (
        await client.get(
            PATH,
            params={"brand_id": brand_id, "product_model_id": model_id},
            headers=_auth(store_id),
        )
    ).json()
    assert body["total_count"] == 1
    assert body["grades"][0]["cost_max"] == "40"
    assert body["grades"][0]["listed_max"] == "120"


async def test_excludes_consignment_items(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """寄售整批排除（裁示 2026-09-09）。

    寄售的架上價是跟寄售人談出來的、店家沒有收購成本；算進件數會讓「收過 N 件」
    與收購價區間的母體對不起來，店員會以為那 N 件都是這個價收的。
    """
    store_id = await _seed_store(db_session)
    brand_id, model_id = await _seed_brand_model(db_session, store_id)
    await _seed_item(
        db_session,
        store_id,
        brand_id=brand_id,
        product_model_id=model_id,
        acquisition_cost=None,
        listed_price="150",
        ownership=OwnershipType.CONSIGNMENT,
        days_ago=5,
    )
    await _seed_item(
        db_session,
        store_id,
        brand_id=brand_id,
        product_model_id=model_id,
        acquisition_cost="40",
        listed_price="120",
        days_ago=20,
    )

    body = (
        await client.get(
            PATH,
            params={"brand_id": brand_id, "product_model_id": model_id},
            headers=_auth(store_id),
        )
    ).json()
    a_grade = body["grades"][0]
    assert body["total_count"] == 1  # 只有買斷那件
    assert a_grade["count"] == 1
    assert a_grade["cost_min"] == "40"
    assert a_grade["cost_max"] == "40"
    # 寄售那件比較新，但售價區間不得被它撐到 150
    assert a_grade["listed_min"] == "120"
    assert a_grade["listed_max"] == "120"
    # 最近一次也不能是寄售那件
    assert body["latest"]["cost"] == "40"
    assert body["latest"]["listed_price"] == "120"


async def test_all_consignment_shows_nothing(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """整款都是寄售 → 沒有可參考的收購行情，回空提示而不是顯示寄售的架上價。"""
    store_id = await _seed_store(db_session)
    brand_id, model_id = await _seed_brand_model(db_session, store_id)
    await _seed_item(
        db_session,
        store_id,
        brand_id=brand_id,
        product_model_id=model_id,
        acquisition_cost=None,
        listed_price="150",
        ownership=OwnershipType.CONSIGNMENT,
    )

    body = (
        await client.get(
            PATH,
            params={"brand_id": brand_id, "product_model_id": model_id},
            headers=_auth(store_id),
        )
    ).json()
    assert body["total_count"] == 0
    assert body["grades"] == []
    assert body["latest"] is None


async def test_falls_back_to_all_time_when_no_recent_data(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """近一年沒收過就退回全部歷史，並明說是舊資料——總比什麼都不顯示好。"""
    store_id = await _seed_store(db_session)
    brand_id, model_id = await _seed_brand_model(db_session, store_id)
    await _seed_item(
        db_session,
        store_id,
        brand_id=brand_id,
        product_model_id=model_id,
        acquisition_cost="30",
        listed_price="90",
        days_ago=500,
    )

    body = (
        await client.get(
            PATH,
            params={"brand_id": brand_id, "product_model_id": model_id},
            headers=_auth(store_id),
        )
    ).json()
    assert body["used_all_time"] is True
    assert body["total_count"] == 1
    assert body["grades"][0]["listed_min"] == "90"


async def test_recent_window_excludes_older_items(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """有近一年的資料時，兩年前的舊行情不得混進來稀釋區間。"""
    store_id = await _seed_store(db_session)
    brand_id, model_id = await _seed_brand_model(db_session, store_id)
    await _seed_item(
        db_session,
        store_id,
        brand_id=brand_id,
        product_model_id=model_id,
        acquisition_cost="10",
        listed_price="30",
        days_ago=800,
    )
    await _seed_item(
        db_session,
        store_id,
        brand_id=brand_id,
        product_model_id=model_id,
        acquisition_cost="40",
        listed_price="120",
        days_ago=15,
    )

    body = (
        await client.get(
            PATH,
            params={"brand_id": brand_id, "product_model_id": model_id},
            headers=_auth(store_id),
        )
    ).json()
    assert body["used_all_time"] is False
    assert body["total_count"] == 1
    assert body["grades"][0]["cost_min"] == "40"
    assert body["grades"][0]["listed_min"] == "120"


async def test_no_history_returns_empty_hint(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """沒收過就是沒收過：回空提示而不是 404，前端才好安靜地不顯示。"""
    store_id = await _seed_store(db_session)
    brand_id, model_id = await _seed_brand_model(db_session, store_id)

    resp = await client.get(
        PATH, params={"brand_id": brand_id, "product_model_id": model_id}, headers=_auth(store_id)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 0
    assert body["grades"] == []
    assert body["latest"] is None


async def test_is_store_scoped(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """他店的成交行情不得外洩（§4）。"""
    store_a = await _seed_store(db_session, "A 店")
    store_b = await _seed_store(db_session, "B 店")
    brand_id, model_id = await _seed_brand_model(db_session, store_a)
    await _seed_item(
        db_session,
        store_a,
        brand_id=brand_id,
        product_model_id=model_id,
        acquisition_cost="40",
        listed_price="120",
    )

    body = (
        await client.get(
            PATH,
            params={"brand_id": brand_id, "product_model_id": model_id},
            headers=_auth(store_b),
        )
    ).json()
    assert body["total_count"] == 0


async def test_other_models_do_not_leak_in(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同品牌不同型號是不同東西，價格不可互相參考。"""
    store_id = await _seed_store(db_session)
    brand_id, model_id = await _seed_brand_model(db_session, store_id)
    other = ProductModel(store_id=store_id, brand_id=brand_id, name="營釘 30cm")
    db_session.add(other)
    await db_session.flush()
    await _seed_item(
        db_session,
        store_id,
        brand_id=brand_id,
        product_model_id=other.id,
        acquisition_cost="80",
        listed_price="250",
    )

    body = (
        await client.get(
            PATH,
            params={"brand_id": brand_id, "product_model_id": model_id},
            headers=_auth(store_id),
        )
    ).json()
    assert body["total_count"] == 0


async def test_requires_brand_and_model(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """沒有品牌＋型號就沒有可靠的比對鍵，拒絕而不是回一堆不相干的價格。"""
    store_id = await _seed_store(db_session)
    resp = await client.get(PATH, params={"brand_id": 1}, headers=_auth(store_id))
    assert resp.status_code == 422


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    resp = await client.get(PATH, params={"brand_id": 1, "product_model_id": 1})
    assert resp.status_code == 401
