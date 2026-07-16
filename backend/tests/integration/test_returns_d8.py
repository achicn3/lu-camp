"""D-8 修復（裁示 2026-07-16）：退貨按比例沖點數＋毛利報表扣退貨。

- 沖點：claw = floor(awarded_points × 退款 ÷ 原總額)；點數不足沖時 clamp 至現有（不擋退貨）。
- 報表：margin_components 依退貨行（退貨發生日落在查詢區間）按比例扣減營收與成本。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import CatalogProduct
from app.modules.inventory.service import InventoryService
from app.modules.sales.service import SalesService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import Grade, OwnershipType, UserRole


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


async def _seed(session: AsyncSession) -> tuple[str, int, int, int]:
    """回 (token, store_id, clerk_id, member_id)；已開帳。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    member = Contact(store_id=store.id, name="會員甲", phone="0911222333", roles=["MEMBER"])
    session.add_all([clerk, member])
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("5000"))
    await session.commit()
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return token, store.id, clerk.id, member.id


def _auth(token: str, *, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


async def _member_points(session: AsyncSession, member_id: int) -> int:
    return int(
        await session.scalar(select(Contact.member_points).where(Contact.id == member_id)) or 0
    )


async def _seed_catalog(session: AsyncSession, store_id: int, *, price: str, qty: int) -> int:
    product = CatalogProduct(
        store_id=store_id, sku="SKU-D8", name="瓦斯罐", unit_price=Decimal(price),
        quantity_on_hand=qty,
    )
    session.add(product)
    await session.flush()
    return product.id


async def test_return_claws_member_points_proportionally(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """total 1000、awarded 10 點；退 300 → 沖 floor(10×300/1000)=3 點。"""
    token, store_id, _, member_id = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="100", qty=20)
    sale_resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 10}],
            "buyer_contact_id": member_id,
        },
        headers=_auth(token, idem="d8-sale-1"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()
    assert await _member_points(db_session, member_id) == 10

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "尺寸不合",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 3}],
        },
        headers=_auth(token, idem="d8-ret-1"),
    )
    assert resp.status_code == 201, resp.text
    db_session.expire_all()
    assert await _member_points(db_session, member_id) == 7  # 10 − floor(10×300/1000)

    # 再退 3 件（累計退 600）→ 再沖 3 點；兩次分開按比例、合計不超過 awarded
    resp2 = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "尺寸不合",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 3}],
        },
        headers=_auth(token, idem="d8-ret-2"),
    )
    assert resp2.status_code == 201, resp2.text
    db_session.expire_all()
    assert await _member_points(db_session, member_id) == 4


async def test_return_points_clamp_when_member_spent_them(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """會員點數已被用掉（餘 1）→ 沖點 clamp 至 1、不阻擋退貨。"""
    token, store_id, _, member_id = await _seed(db_session)
    catalog_id = await _seed_catalog(db_session, store_id, price="100", qty=20)
    sale_resp = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 10}],
            "buyer_contact_id": member_id,
        },
        headers=_auth(token, idem="d8-sale-2"),
    )
    assert sale_resp.status_code == 201
    sale = sale_resp.json()
    # 模擬點數已被花掉：直接把餘額壓到 1
    member = await db_session.get(Contact, member_id)
    assert member is not None
    member.member_points = 1
    await db_session.commit()

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "商品瑕疵",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 5}],
        },
        headers=_auth(token, idem="d8-ret-3"),
    )
    assert resp.status_code == 201, resp.text
    db_session.expire_all()
    assert await _member_points(db_session, member_id) == 0  # clamp：只沖得掉 1


async def test_margin_breakdown_subtracts_returns_in_window(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """毛利報表扣退貨：自有序號品售 3000/成本 500，退貨後營收與成本雙扣。"""
    token, store_id, _, _ = await _seed(db_session)
    item = await InventoryService(db_session).create_serialized_item(
        store_id,
        item_code="S1-D8TEST01",
        name="二手睡墊",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal("3000"),
        acquisition_cost=Decimal("500"),
    )
    await db_session.commit()
    sale_resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "SERIALIZED", "item_code": "S1-D8TEST01"}]},
        headers=_auth(token, idem="d8-sale-3"),
    )
    assert sale_resp.status_code == 201, sale_resp.text
    sale = sale_resp.json()

    svc = SalesService(db_session)
    t0 = datetime.now(UTC) - timedelta(days=1)
    t1 = datetime.now(UTC) + timedelta(days=1)
    before = await svc.margin_breakdown(store_id, t0, t1)
    assert before.recognized_revenue == Decimal("3000")
    assert before.owned_cogs == Decimal("500")
    assert before.gross_margin == Decimal("2500")

    resp = await client.post(
        "/api/v1/returns",
        json={
            "sale_id": sale["id"],
            "reason": "客人反悔",
            "lines": [{"sale_line_id": sale["lines"][0]["id"], "qty": 1}],
        },
        headers=_auth(token, idem="d8-ret-4"),
    )
    assert resp.status_code == 201, resp.text
    db_session.expire_all()

    after = await svc.margin_breakdown(store_id, t0, t1)
    assert after.recognized_revenue == Decimal("0")
    assert after.owned_cogs == Decimal("0")
    assert after.gross_margin == Decimal("0")

    # 退貨日不在查詢區間 → 不影響該區間（退貨歸屬退貨發生日）
    only_sale_window = await svc.margin_breakdown(
        store_id, t0 - timedelta(days=2), t0
    )
    assert only_sale_window.recognized_revenue == Decimal("0")  # 窗外皆無
    full_again = await svc.margin_breakdown(store_id, t0, t1)
    assert full_again.gross_turnover == Decimal("0")

    _ = item
