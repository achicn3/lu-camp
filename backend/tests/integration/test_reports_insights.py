"""經營洞察報表（#8）整合測試：品牌/類型暢銷彙整、毛利口徑、周轉摘要。MANAGER 限定。"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.inventory.models import Brand, Category, SerializedItem
from app.modules.sales.models import Sale, SaleLine
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    Grade,
    OwnershipType,
    SaleLineType,
    SerializedItemStatus,
    UserRole,
)

pytestmark = pytest.mark.asyncio


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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _sell(
    session: AsyncSession, store_id: int, clerk_id: int, item: SerializedItem, price: Decimal
) -> None:
    sale = Sale(
        store_id=store_id, clerk_user_id=clerk_id,
        subtotal=price, tax=Decimal(0), total=price,
        created_at=datetime(2026, 6, 20, tzinfo=UTC),
    )
    session.add(sale)
    await session.flush()
    session.add(
        SaleLine(
            store_id=store_id, sale_id=sale.id, line_type=SaleLineType.SERIALIZED,
            serialized_item_id=item.id, description=item.name, qty=1,
            unit_price=price, line_total=price,
        )
    )
    await session.flush()


async def test_insights_brand_breakdown_units_revenue_margin(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store = Store(name="門市")
    db_session.add(store)
    await db_session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    brand = Brand(store_id=store.id, name="Snow Peak")
    cat = Category(store_id=store.id, name="帳篷", target_margin_pct=40)
    db_session.add_all([mgr, clerk, brand, cat])
    await db_session.flush()
    token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)

    owned = SerializedItem(
        store_id=store.id, item_code="OWN-1", name="買斷帳篷", grade=Grade.A,
        ownership_type=OwnershipType.OWNED, listed_price=Decimal(5800),
        acquisition_cost=Decimal(3000), brand_id=brand.id, category_id=cat.id,
        status=SerializedItemStatus.SOLD,
        intake_date=datetime(2026, 1, 1, tzinfo=UTC), sold_date=datetime(2026, 6, 20, tzinfo=UTC),
    )
    consign = SerializedItem(
        store_id=store.id, item_code="CON-1", name="寄售帳篷", grade=Grade.A,
        ownership_type=OwnershipType.CONSIGNMENT, listed_price=Decimal(2000),
        commission_pct=50, brand_id=brand.id, category_id=cat.id,
        status=SerializedItemStatus.SOLD,
        intake_date=datetime(2026, 6, 1, tzinfo=UTC), sold_date=datetime(2026, 6, 20, tzinfo=UTC),
    )
    db_session.add_all([owned, consign])
    await db_session.flush()
    await _sell(db_session, store.id, clerk.id, owned, Decimal(5220))  # 折後成交
    await _sell(db_session, store.id, clerk.id, consign, Decimal(2000))

    resp = await client.get(
        "/api/v1/reports/insights",
        params={"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T00:00:00Z"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rows = {r["label"]: r for r in body["brand_breakdown"]}
    snow = rows["Snow Peak"]
    assert snow["units_sold"] == 2
    assert snow["revenue"] == "7220"  # 5220 + 2000
    # 毛利：買斷 5220-3000=2220 ＋ 寄售抽成 round(2000*50/100)=1000 → 3220
    assert snow["margin"] == "3220"
    assert snow["avg_unit_price"] == "3610"  # 7220 / 2
    assert snow["avg_days_in_stock"] is not None

    cats = {r["label"]: r for r in body["category_breakdown"]}
    assert cats["帳篷"]["units_sold"] == 2
    # 周轉摘要存在（實際數值依當下在庫，僅驗結構）。
    assert "in_stock_over_90d" in body["turnover"]
    assert body["turnover"]["consignment_serialized"] >= 0


async def test_insights_requires_manager(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    store = Store(name="門市")
    db_session.add(store)
    await db_session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    db_session.add(clerk)
    await db_session.flush()
    token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    resp = await client.get(
        "/api/v1/reports/insights",
        params={"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T00:00:00Z"},
        headers=_auth(token),
    )
    assert resp.status_code == 403
