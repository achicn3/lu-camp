"""R6 財務趨勢時間序列整合測試（docs/19 R6）：

day/week/month/quarter 分桶；桶 KPI 與 sales-margin 同源（日桶加總 = 全期）；季跨年；空桶補 0；
過多桶 → 422；to<=from / 非法粒度 → 422；現金支出/購物金入桶；MANAGER；匯出。

直接建構 sales（含 created_at）以控制落桶——deferred tender 守衛僅於 COMMIT 檢查，
測試以 rollback 隔離不 commit，故不觸發。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.core.time import store_bucket_bounds
from app.main import create_app
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import SerializedItem
from app.modules.menu.models import MenuItem
from app.modules.sales.models import Sale, SaleLine
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import (
    CashMovementType,
    Grade,
    OwnershipType,
    SaleLineType,
    SerializedItemStatus,
    StoreCreditSourceType,
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


async def _seed(session: AsyncSession) -> tuple[str, str, int, int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    member = Contact(store_id=store.id, name="會員", roles=["MEMBER"], national_id_enc="enc")
    session.add_all([mgr, clerk, member])
    await session.flush()
    return (
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        store.id,
        clerk.id,
        member.id,
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


_SEQ = 0


async def _add_owned_sale(
    session: AsyncSession,
    store_id: int,
    clerk_id: int,
    *,
    when: datetime,
    total: str,
    cost: str,
) -> None:
    """直接建一筆「自有序號品」已售銷售，created_at=when（控制落桶）。"""
    global _SEQ
    _SEQ += 1
    item = SerializedItem(
        store_id=store_id,
        item_code=f"TR-{_SEQ}",
        name="自有序號品",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        acquisition_cost=Decimal(cost),
        listed_price=Decimal(total),
        status=SerializedItemStatus.SOLD,
    )
    session.add(item)
    await session.flush()
    sale = Sale(
        store_id=store_id,
        clerk_user_id=clerk_id,
        subtotal=Decimal(total),
        tax=Decimal(0),
        total=Decimal(total),
        created_at=when,
    )
    session.add(sale)
    await session.flush()
    session.add(
        SaleLine(
            store_id=store_id,
            sale_id=sale.id,
            line_type=SaleLineType.SERIALIZED,
            serialized_item_id=item.id,
            description="自有序號品",
            qty=1,
            unit_price=Decimal(total),
            line_total=Decimal(total),
        )
    )
    await session.flush()


async def _add_menu_sale(
    session: AsyncSession,
    store_id: int,
    clerk_id: int,
    *,
    when: datetime,
    total: str,
) -> None:
    """直接建一筆「餐飲品項」已售銷售，created_at=when（控制落桶）。"""
    global _SEQ
    _SEQ += 1
    menu_item = MenuItem(
        store_id=store_id,
        name=f"手沖咖啡-{_SEQ}",
        unit_price=Decimal(total),
        is_available=True,
        sort_order=0,
    )
    session.add(menu_item)
    await session.flush()
    sale = Sale(
        store_id=store_id,
        clerk_user_id=clerk_id,
        subtotal=Decimal(total),
        tax=Decimal(0),
        total=Decimal(total),
        created_at=when,
    )
    session.add(sale)
    await session.flush()
    session.add(
        SaleLine(
            store_id=store_id,
            sale_id=sale.id,
            line_type=SaleLineType.MENU,
            menu_item_id=menu_item.id,
            description="手沖咖啡",
            qty=1,
            unit_price=Decimal(total),
            line_total=Decimal(total),
        )
    )
    await session.flush()


async def test_food_secondhand_split_sums_to_period(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """餐飲/二手分列：日桶 food_revenue/secondhand_revenue 加總 = 全期 sales-margin，
    且 food + secondhand == recognized_revenue（同源、不重複認列）。"""
    mgr, _clerk, store_id, clerk_id, _m = await _seed(db_session)
    await _add_owned_sale(
        db_session, store_id, clerk_id, when=datetime(2026, 5, 2, tzinfo=UTC),
        total="500", cost="300",
    )
    await _add_menu_sale(
        db_session, store_id, clerk_id, when=datetime(2026, 5, 3, tzinfo=UTC), total="120",
    )
    await _add_menu_sale(
        db_session, store_id, clerk_id, when=datetime(2026, 5, 4, tzinfo=UTC), total="80",
    )
    rows = await _trends(
        client, mgr, dfrom="2026-05-01T00:00:00Z", dto="2026-05-31T00:00:00Z", gran="day"
    )
    total_food = sum(Decimal(str(r["food_revenue"])) for r in rows)
    total_2nd = sum(Decimal(str(r["secondhand_revenue"])) for r in rows)
    total_recognized = sum(Decimal(str(r["recognized_revenue"])) for r in rows)
    margin = (
        await client.get(
            "/api/v1/reports/sales-margin",
            params={"from": "2026-05-01T00:00:00Z", "to": "2026-05-31T00:00:00Z"},
            headers=_auth(mgr),
        )
    ).json()
    assert total_food == Decimal(margin["food_revenue"]) == Decimal(200)  # 120 + 80
    assert total_2nd == Decimal(margin["secondhand_revenue"]) == Decimal(500)
    assert total_food + total_2nd == total_recognized == Decimal(700)


async def _trends(
    client: httpx.AsyncClient, mgr: str, *, dfrom: str, dto: str, gran: str
) -> list[dict[str, object]]:
    resp = await client.get(
        "/api/v1/reports/trends",
        params={"from": dfrom, "to": dto, "granularity": gran},
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    rows: list[dict[str, object]] = resp.json()["rows"]
    return rows


async def test_monthly_buckets(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _clerk, store_id, clerk_id, _m = await _seed(db_session)
    await _add_owned_sale(
        db_session,
        store_id,
        clerk_id,
        when=datetime(2026, 1, 15, tzinfo=UTC),
        total="500",
        cost="300",
    )
    await _add_owned_sale(
        db_session,
        store_id,
        clerk_id,
        when=datetime(2026, 2, 10, tzinfo=UTC),
        total="700",
        cost="400",
    )
    rows = await _trends(
        client, mgr, dfrom="2025-12-31T16:00:00Z", dto="2026-02-28T16:00:00Z", gran="month"
    )
    assert len(rows) == 2
    assert rows[0]["period"] == "2026-01-01"
    assert rows[0]["gross_turnover"] == "500"
    assert rows[0]["gross_margin"] == "200"
    assert rows[1]["period"] == "2026-02-01"
    assert rows[1]["gross_turnover"] == "700"
    assert rows[1]["gross_margin"] == "300"


async def test_day_buckets_sum_equals_period(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同源：日桶 KPI 加總 = 全期 sales-margin。"""
    mgr, _clerk, store_id, clerk_id, _m = await _seed(db_session)
    await _add_owned_sale(
        db_session,
        store_id,
        clerk_id,
        when=datetime(2026, 5, 2, tzinfo=UTC),
        total="500",
        cost="300",
    )
    await _add_owned_sale(
        db_session,
        store_id,
        clerk_id,
        when=datetime(2026, 5, 4, tzinfo=UTC),
        total="800",
        cost="500",
    )
    rows = await _trends(
        client, mgr, dfrom="2026-05-01T00:00:00Z", dto="2026-05-31T00:00:00Z", gran="day"
    )
    total_margin = sum(Decimal(str(r["gross_margin"])) for r in rows)
    total_turnover = sum(Decimal(str(r["gross_turnover"])) for r in rows)
    margin = (
        await client.get(
            "/api/v1/reports/sales-margin",
            params={"from": "2026-05-01T00:00:00Z", "to": "2026-05-31T00:00:00Z"},
            headers=_auth(mgr),
        )
    ).json()
    assert total_margin == Decimal(margin["gross_margin"]) == Decimal(500)  # 200 + 300
    assert total_turnover == Decimal(margin["gross_turnover"]) == Decimal(1300)


async def test_day_buckets_align_to_taipei_midnight(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, store_id, clerk_id, _m = await _seed(db_session)
    await _add_owned_sale(
        db_session,
        store_id,
        clerk_id,
        when=datetime(2026, 7, 21, 16, 30, tzinfo=UTC),
        total="500",
        cost="300",
    )
    await _add_owned_sale(
        db_session,
        store_id,
        clerk_id,
        when=datetime(2026, 7, 22, 16, 30, tzinfo=UTC),
        total="700",
        cost="400",
    )

    rows = await _trends(
        client,
        mgr,
        dfrom="2026-07-21T16:00:00Z",
        dto="2026-07-23T16:00:00Z",
        gran="day",
    )

    assert [row["period"] for row in rows] == ["2026-07-22", "2026-07-23"]
    assert [row["gross_turnover"] for row in rows] == ["500", "700"]


async def test_quarter_buckets_cross_year(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, store_id, clerk_id, _m = await _seed(db_session)
    await _add_owned_sale(
        db_session,
        store_id,
        clerk_id,
        when=datetime(2025, 11, 5, tzinfo=UTC),
        total="500",
        cost="300",
    )
    await _add_owned_sale(
        db_session,
        store_id,
        clerk_id,
        when=datetime(2026, 2, 5, tzinfo=UTC),
        total="900",
        cost="600",
    )
    rows = await _trends(
        client,
        mgr,
        dfrom="2025-09-30T16:00:00Z",
        dto="2026-03-31T16:00:00Z",
        gran="quarter",
    )
    assert [r["period"] for r in rows] == ["2025-10-01", "2026-01-01"]
    assert rows[0]["gross_turnover"] == "500"
    assert rows[1]["gross_turnover"] == "900"


async def test_empty_buckets_are_zero_filled(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store_id, _clerk_id, _m = await _seed(db_session)
    rows = await _trends(
        client, mgr, dfrom="2025-12-31T16:00:00Z", dto="2026-03-31T16:00:00Z", gran="month"
    )
    assert [r["period"] for r in rows] == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert all(r["gross_turnover"] == "0" and r["gross_margin"] == "0" for r in rows)
    assert all(r["gross_margin_rate"] is None for r in rows)


async def test_cash_out_and_store_credit_in_bucket(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """現金支出（收購付現）與購物金發出落入當期桶（以服務於 now 寫入）。"""
    mgr, _clerk, store_id, clerk_id, member_id = await _seed(db_session)
    cash = CashDrawerService(db_session)
    await cash.open_session(store_id, clerk_id, Decimal(1000))
    await cash.record_movement(store_id, CashMovementType.BUYOUT_OUT, Decimal(250))
    await StoreCreditService(db_session).credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(400),
        premium_rate=Decimal(0),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=1,
        created_by=clerk_id,
    )
    dfrom_dt, dto_dt = store_bucket_bounds("month", datetime.now(UTC))
    dfrom = dfrom_dt.isoformat()
    dto = dto_dt.isoformat()
    rows = await _trends(client, mgr, dfrom=dfrom, dto=dto, gran="month")
    assert len(rows) == 1
    assert rows[0]["total_cash_out"] == "250"
    assert rows[0]["store_credit_issued"] == "400"


async def test_trends_rejects_bad_range_and_granularity(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store_id, _clerk_id, _m = await _seed(db_session)
    bad = await client.get(
        "/api/v1/reports/trends",
        params={"from": "2026-02-01T00:00:00Z", "to": "2026-01-01T00:00:00Z", "granularity": "day"},
        headers=_auth(mgr),
    )
    assert bad.status_code == 422
    badg = await client.get(
        "/api/v1/reports/trends",
        params={
            "from": "2026-01-01T00:00:00Z",
            "to": "2026-02-01T00:00:00Z",
            "granularity": "year",
        },
        headers=_auth(mgr),
    )
    assert badg.status_code == 422


async def test_trends_rejects_naive_datetime_params(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """瞬間型查詢必須帶 offset；不得依賴伺服器或資料庫時區猜測。"""
    mgr, _clerk, _store_id, _clerk_id, _m = await _seed(db_session)
    for params in (
        {"from": "2026-01-01", "to": "2026-02-01", "granularity": "day"},
        {"from": "2026-01-01T00:00:00", "to": "2026-02-01T00:00:00", "granularity": "month"},
    ):
        resp = await client.get(
            "/api/v1/reports/trends", params=params, headers=_auth(mgr)
        )
        assert resp.status_code == 422, resp.text


async def test_trends_rejects_too_many_buckets(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store_id, _clerk_id, _m = await _seed(db_session)
    resp = await client.get(
        "/api/v1/reports/trends",
        params={"from": "2020-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z", "granularity": "day"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 422


async def test_trends_manager_only_and_csv(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, store_id, clerk_id, _m = await _seed(db_session)
    await _add_owned_sale(
        db_session,
        store_id,
        clerk_id,
        when=datetime(2026, 1, 5, tzinfo=UTC),
        total="500",
        cost="300",
    )
    forbidden = await client.get(
        "/api/v1/reports/trends",
        params={
            "from": "2026-01-01T00:00:00Z",
            "to": "2026-02-01T00:00:00Z",
            "granularity": "month",
        },
        headers=_auth(clerk),
    )
    assert forbidden.status_code == 403
    csv_resp = await client.get(
        "/api/v1/reports/trends",
        params={
            "from": "2026-01-01T00:00:00Z",
            "to": "2026-02-01T00:00:00Z",
            "granularity": "month",
            "format": "csv",
        },
        headers=_auth(mgr),
    )
    assert csv_resp.status_code == 200
    text = csv_resp.content.decode("utf-8-sig")
    assert "營業額" in text and "毛利" in text and "期間" in text
