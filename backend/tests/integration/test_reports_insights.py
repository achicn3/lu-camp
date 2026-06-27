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
from app.modules.inventory.models import Brand, BulkLot, Category, SerializedItem
from app.modules.sales.models import Sale, SaleLine
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
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


async def test_insights_includes_bulk_sales(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """散裝售出也納入品牌/類型排行（Codex P2）：件數=售出件數、毛利=成交−每件成本×件數。"""
    store = Store(name="門市")
    db_session.add(store)
    await db_session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    brand = Brand(store_id=store.id, name="Coleman")
    cat = Category(store_id=store.id, name="營釘", target_margin_pct=40)
    db_session.add_all([mgr, clerk, brand, cat])
    await db_session.flush()
    token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)

    # 成本 100／總 3、賣 2：整行四捨五入 COGS=round(100*2/3)=67（每件先捨會錯成 66）。
    lot = BulkLot(
        store_id=store.id, lot_code="LOT-1", name="鋁合金營釘", grade=Grade.E,
        acquisition_cost=Decimal(100), acquisition_basis=BulkAcquisitionBasis.BAG,
        unit_price=Decimal(50), total_qty=3, remaining_qty=1, status=BulkLotStatus.ON_SALE,
        brand_id=brand.id, category_id=cat.id, intake_date=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db_session.add(lot)
    await db_session.flush()
    sale = Sale(
        store_id=store.id, clerk_user_id=clerk.id,
        subtotal=Decimal(100), tax=Decimal(0), total=Decimal(100),
        created_at=datetime(2026, 6, 20, tzinfo=UTC),
    )
    db_session.add(sale)
    await db_session.flush()
    db_session.add(
        SaleLine(
            store_id=store.id, sale_id=sale.id, line_type=SaleLineType.BULK_LOT,
            bulk_lot_id=lot.id, description=lot.name, qty=2,
            unit_price=Decimal(50), line_total=Decimal(100),
        )
    )
    await db_session.flush()

    resp = await client.get(
        "/api/v1/reports/insights",
        params={"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T00:00:00Z"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    rows = {r["label"]: r for r in resp.json()["brand_breakdown"]}
    assert "Coleman" in rows  # 散裝品牌有進排行
    coleman = rows["Coleman"]
    assert coleman["units_sold"] == 2  # 售出 2 件（非 1 列）
    assert coleman["revenue"] == "100"
    # 整行四捨五入 COGS=round(100*2/3)=67 → 毛利 100 - 67 = 33（Codex P3）。
    assert coleman["margin"] == "33"


async def test_insights_turnover_days_weighted_by_units(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """平均在庫天數以售出件數加權（Codex P2）：1 件序號品(10天)＋5 件散裝(100天)→(10+500)/6=85。"""
    store = Store(name="門市")
    db_session.add(store)
    await db_session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    brand = Brand(store_id=store.id, name="MSR")
    db_session.add_all([mgr, clerk, brand])
    await db_session.flush()
    token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)

    item = SerializedItem(
        store_id=store.id, item_code="OWN-W", name="爐具", grade=Grade.A,
        ownership_type=OwnershipType.OWNED, listed_price=Decimal(1000),
        acquisition_cost=Decimal(500), brand_id=brand.id, status=SerializedItemStatus.SOLD,
        intake_date=datetime(2026, 6, 10, tzinfo=UTC), sold_date=datetime(2026, 6, 20, tzinfo=UTC),
    )
    lot = BulkLot(
        store_id=store.id, lot_code="LOT-W", name="瓦斯", grade=Grade.E,
        acquisition_cost=Decimal(50), acquisition_basis=BulkAcquisitionBasis.BAG,
        unit_price=Decimal(40), total_qty=10, remaining_qty=5, status=BulkLotStatus.ON_SALE,
        brand_id=brand.id, intake_date=datetime(2026, 3, 12, tzinfo=UTC),  # → 6/20 約 100 天
    )
    db_session.add_all([item, lot])
    await db_session.flush()
    await _sell(db_session, store.id, clerk.id, item, Decimal(1000))  # 序號品：10 天、1 件
    sale = Sale(
        store_id=store.id, clerk_user_id=clerk.id,
        subtotal=Decimal(200), tax=Decimal(0), total=Decimal(200),
        created_at=datetime(2026, 6, 20, tzinfo=UTC),
    )
    db_session.add(sale)
    await db_session.flush()
    db_session.add(
        SaleLine(
            store_id=store.id, sale_id=sale.id, line_type=SaleLineType.BULK_LOT,
            bulk_lot_id=lot.id, description=lot.name, qty=5,
            unit_price=Decimal(40), line_total=Decimal(200),
        )
    )
    await db_session.flush()

    resp = await client.get(
        "/api/v1/reports/insights",
        params={"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T00:00:00Z"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    msr = {r["label"]: r for r in resp.json()["brand_breakdown"]}["MSR"]
    # 加權：(10×1 + 100×5) / (1+5) = 510/6 = 85.0（未加權會是 (10+100)/2=55）。
    assert msr["avg_days_in_stock"] == 85.0


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
