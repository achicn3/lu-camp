"""R5 每日營運儀表板整合測試（docs/19 R5）：

組合 R1 現金 + R2 毛利的同源數字（逐欄與 daily-cash / sales-margin 端點交叉一致）；
營業額 vs 認列營收區分；稅推算；估算淨利標註；客單價；購物金非現金；空日；MANAGER；唯讀；匯出。
"""

import calendar
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.money import round_ntd, split_tax_inclusive
from app.core.security import encode_access_token
from app.core.time import store_date, store_day_bounds
from app.main import create_app
from app.modules.cashdrawer.models import CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.inventory.models import SerializedItem
from app.modules.sales.models import Sale
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import Grade, OwnershipType, SerializedItemStatus, UserRole


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


async def _seed(session: AsyncSession) -> tuple[str, str, int, int]:
    """建店＋MANAGER＋CLERK（開帳）＋寄售人，回 (mgr, clerk, store_id, consignor_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    consignor = Contact(store_id=store.id, name="寄售人", roles=["SELLER"], national_id_enc="enc")
    session.add_all([mgr, clerk, consignor])
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal(1000))
    return (
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        store.id,
        consignor.id,
    )


def _auth(token: str, *, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


async def _day(session: AsyncSession, store_id: int) -> str:
    cs = await session.scalar(select(CashSession).where(CashSession.store_id == store_id))
    assert cs is not None
    return store_date(cs.opened_at).isoformat()


async def _add_owned_serialized(
    session: AsyncSession, store_id: int, *, code: str, cost: str, price: str
) -> None:
    session.add(
        SerializedItem(
            store_id=store_id,
            item_code=code,
            name="自有序號品",
            grade=Grade.A,
            ownership_type=OwnershipType.OWNED,
            acquisition_cost=Decimal(cost),
            listed_price=Decimal(price),
            status=SerializedItemStatus.IN_STOCK,
        )
    )
    await session.flush()


async def _add_consignment_serialized(
    session: AsyncSession, store_id: int, consignor_id: int, *, code: str, price: str, pct: int
) -> None:
    session.add(
        SerializedItem(
            store_id=store_id,
            item_code=code,
            name="寄售序號品",
            grade=Grade.A,
            ownership_type=OwnershipType.CONSIGNMENT,
            consignor_id=consignor_id,
            commission_pct=pct,
            listed_price=Decimal(price),
            status=SerializedItemStatus.IN_STOCK,
        )
    )
    await session.flush()


async def _sell_serialized(client: httpx.AsyncClient, token: str, code: str, *, key: str) -> None:
    resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "SERIALIZED", "item_code": code}]},
        headers=_auth(token, idem=key),
    )
    assert resp.status_code == 201, resp.text


async def _summary(client: httpx.AsyncClient, mgr: str, day: str) -> dict[str, object]:
    resp = await client.get(
        "/api/v1/reports/daily-summary", params={"date": day}, headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    return body


async def test_daily_summary_cross_consistent_with_r1_r2(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """同源驗證：R5 的現金欄 == daily-cash、毛利欄 == sales-margin。"""
    mgr, clerk, store_id, _consignor = await _seed(db_session)
    await _add_owned_serialized(db_session, store_id, code="OWN-1", cost="300", price="500")
    await _sell_serialized(client, clerk, "OWN-1", key="s1")
    day = await _day(db_session, store_id)
    day_start, day_end = store_day_bounds(date.fromisoformat(day))

    summary = await _summary(client, mgr, day)
    cash = (
        await client.get("/api/v1/reports/daily-cash", params={"date": day}, headers=_auth(mgr))
    ).json()
    margin = (
        await client.get(
            "/api/v1/reports/sales-margin",
            params={"from": day_start.isoformat(), "to": day_end.isoformat()},
            headers=_auth(mgr),
        )
    ).json()

    # 毛利欄與 sales-margin 同源
    assert summary["gross_turnover"] == margin["gross_turnover"] == "500"
    assert summary["recognized_revenue"] == margin["recognized_revenue"] == "500"
    assert summary["gross_margin"] == margin["gross_margin"] == "200"
    assert summary["gross_margin_rate"] == margin["gross_margin_rate"]
    assert summary["cogs"] == "300"  # owned_cogs + bulk_cogs
    # 現金欄與 daily-cash 同源
    assert summary["cash_sales_in"] == cash["total_cash_sales"] == "500"
    assert summary["expected_cash"] == cash["total_expected"]  # 1000 + 500
    assert summary["expected_cash"] == "1500"
    # 稅：認列營收 500 含稅 → 推算一次
    net, tax = split_tax_inclusive(Decimal(500), Decimal("0.05"))
    assert summary["net_sales_ex_tax"] == str(net)
    assert summary["tax"] == str(tax)
    # 客單價 = 營業額 ÷ 筆數
    assert summary["transaction_count"] == 1
    assert summary["avg_ticket"] == "500"


async def test_daily_summary_uses_taipei_business_day(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, store_id, _consignor = await _seed(db_session)
    session = await db_session.scalar(select(CashSession).where(CashSession.store_id == store_id))
    assert session is not None
    session.opened_at = datetime(2026, 7, 21, 16, 30, tzinfo=UTC)
    await _add_owned_serialized(db_session, store_id, code="OWN-TZ", cost="300", price="500")
    await _sell_serialized(client, clerk, "OWN-TZ", key="tz-sale")
    sale = await db_session.scalar(select(Sale).where(Sale.store_id == store_id))
    assert sale is not None
    sale.created_at = datetime(2026, 7, 21, 16, 40, tzinfo=UTC)  # 台灣 07-22 00:40
    await db_session.flush()

    taipei_day = await _summary(client, mgr, "2026-07-22")
    previous_day = await _summary(client, mgr, "2026-07-21")

    assert taipei_day["gross_turnover"] == "500"
    assert taipei_day["transaction_count"] == 1
    assert previous_day["gross_turnover"] == "0"


async def test_daily_summary_consignment_turnover_vs_recognized(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, store_id, consignor = await _seed(db_session)
    await _add_consignment_serialized(
        db_session, store_id, consignor, code="CON-1", price="1000", pct=50
    )
    await _sell_serialized(client, clerk, "CON-1", key="c1")
    day = await _day(db_session, store_id)

    s = await _summary(client, mgr, day)
    assert s["gross_turnover"] == "1000"  # 流水認全額
    assert s["recognized_revenue"] == "500"  # 只認抽成
    assert s["consignment_commission_income"] == "500"
    assert s["gross_margin"] == "500"


async def test_daily_summary_estimated_net_income(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """設了月固定支出 → 估算淨利 = 毛利 − round_ntd(月支出 ÷ 當月天數)；未設 → null。"""
    mgr, clerk, store_id, _consignor = await _seed(db_session)
    await _add_owned_serialized(db_session, store_id, code="OWN-N", cost="300", price="500")
    await _sell_serialized(client, clerk, "OWN-N", key="n1")
    day = await _day(db_session, store_id)

    # 未設月固定支出 → None
    assert (await _summary(client, mgr, day))["estimated_net_income"] is None

    patched = await client.patch(
        "/api/v1/settings",
        json={"monthly_fixed_cash_outflow": "30000"},
        headers=_auth(mgr),
    )
    assert patched.status_code == 200, patched.text
    s = await _summary(client, mgr, day)
    year, month = int(day[:4]), int(day[5:7])
    days = calendar.monthrange(year, month)[1]
    prorated = round_ntd(Decimal(30000) / Decimal(days))
    assert s["estimated_net_income"] == str(200 - prorated)  # 毛利 200 − 攤提


async def test_daily_summary_empty_day(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _clerk, _store_id, _consignor = await _seed(db_session)
    s = await _summary(client, mgr, "2026-06-20")
    assert s["gross_turnover"] == "0"
    assert s["gross_margin"] == "0"
    assert s["gross_margin_rate"] is None
    assert s["avg_ticket"] is None
    assert s["transaction_count"] == 0


async def test_daily_summary_is_read_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, store_id, _consignor = await _seed(db_session)
    await _add_owned_serialized(db_session, store_id, code="OWN-R", cost="300", price="500")
    await _sell_serialized(client, clerk, "OWN-R", key="r1")
    day = await _day(db_session, store_id)

    before = await db_session.scalar(select(func.count()).select_from(Sale))
    await _summary(client, mgr, day)
    after = await db_session.scalar(select(func.count()).select_from(Sale))
    assert before == after


async def test_daily_summary_manager_only_and_export(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, store_id, _consignor = await _seed(db_session)
    await _add_owned_serialized(db_session, store_id, code="OWN-C", cost="300", price="500")
    await _sell_serialized(client, clerk, "OWN-C", key="c1")
    day = await _day(db_session, store_id)

    forbidden = await client.get(
        "/api/v1/reports/daily-summary", params={"date": day}, headers=_auth(clerk)
    )
    assert forbidden.status_code == 403

    csv_resp = await client.get(
        "/api/v1/reports/daily-summary",
        params={"date": day, "format": "csv"},
        headers=_auth(mgr),
    )
    assert csv_resp.status_code == 200
    text = csv_resp.content.decode("utf-8-sig")
    assert "毛利" in text and "營業額" in text and "估算淨利" in text
