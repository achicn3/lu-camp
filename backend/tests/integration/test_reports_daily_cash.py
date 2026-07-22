"""R1 每日現金對帳報表整合測試（docs/19 §2.2）：

session 分列 + 當日合計；expected 與關帳同公式（含 ACQUISITION_VOID_IN）；
購物金兌付只展示不進 expected；無 session 日回空（非 500）；匯出；MANAGER only；唯讀。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import CashMovementType, StoreCreditSourceType, UserRole


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
    member = Contact(store_id=store.id, name="會員甲", roles=["MEMBER"], national_id_enc="enc")
    session.add_all([mgr, clerk, member])
    await session.flush()
    mgr_token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
    clerk_token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return mgr_token, clerk_token, store.id, member.id, mgr.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _today_iso(session: CashSession) -> str:
    return session.opened_at.date().isoformat()


async def test_daily_cash_empty_day_returns_empty_not_500(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store_id, _member_id, _mgr_id = await _seed(db_session)
    resp = await client.get(
        "/api/v1/reports/daily-cash", params={"date": "2026-06-20"}, headers=_auth(mgr)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sessions"] == []
    assert body["total_expected"] == "0"
    assert body["total_cash_sales"] == "0"
    assert body["total_store_credit_redeemed_display_only"] == "0"


async def test_daily_cash_assigns_session_to_taipei_opening_date(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, store_id, _member_id, mgr_id = await _seed(db_session)
    session = await CashDrawerService(db_session).open_session(store_id, mgr_id, Decimal(1000))
    session.opened_at = datetime(2026, 7, 21, 16, 30, tzinfo=UTC)  # 台灣 07-22 00:30
    await db_session.flush()

    taipei_day = await client.get(
        "/api/v1/reports/daily-cash", params={"date": "2026-07-22"}, headers=_auth(mgr)
    )
    previous_day = await client.get(
        "/api/v1/reports/daily-cash", params={"date": "2026-07-21"}, headers=_auth(mgr)
    )

    assert taipei_day.status_code == 200
    assert [row["session_id"] for row in taipei_day.json()["sessions"]] == [session.id]
    assert previous_day.status_code == 200
    assert previous_day.json()["sessions"] == []


async def test_daily_cash_expected_matches_close_formula(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """expected = 開帳 + SALE_IN + ACQUISITION_VOID_IN − BUYOUT_OUT − MANUAL_ADJUST(±)；
    且關帳後報表 expected == session.expected_amount（同源）。"""
    mgr, _clerk, store_id, _member_id, mgr_id = await _seed(db_session)
    cash = CashDrawerService(db_session)
    session = await cash.open_session(store_id, mgr_id, Decimal(1000))
    await cash.record_movement(store_id, CashMovementType.SALE_IN, Decimal(500))
    await cash.record_movement(store_id, CashMovementType.ACQUISITION_VOID_IN, Decimal(100))
    await cash.record_movement(store_id, CashMovementType.BUYOUT_OUT, Decimal(200))
    await cash.record_movement(
        store_id, CashMovementType.MANUAL_ADJUST, Decimal(-50), actor_user_id=mgr_id
    )
    day = _today_iso(session)

    # 開帳中：即時 expected = 1000+500+100-200-50 = 1350
    body = (
        await client.get("/api/v1/reports/daily-cash", params={"date": day}, headers=_auth(mgr))
    ).json()
    assert len(body["sessions"]) == 1
    row = body["sessions"][0]
    assert row["status"] == "OPEN"
    assert row["cash_sales"] == "500"
    assert row["acquisition_void_in"] == "100"
    assert row["buyout_out"] == "200"
    assert row["manual_adjust_total"] == "-50"
    assert row["expected_amount"] == "1350"
    assert row["counted_amount"] is None
    assert row["variance"] is None

    # 關帳後：報表 expected 取已落帳值，與 session.expected_amount 同源
    closed = await cash.close_session(session, counted_amount=Decimal(1340), closed_by=mgr_id)
    assert closed.expected_amount == Decimal(1350)
    body2 = (
        await client.get("/api/v1/reports/daily-cash", params={"date": day}, headers=_auth(mgr))
    ).json()
    row2 = body2["sessions"][0]
    assert row2["status"] == "CLOSED"
    assert row2["expected_amount"] == "1350"
    assert row2["counted_amount"] == "1340"
    assert row2["variance"] == "-10"
    assert body2["total_variance"] == "-10"


async def test_daily_cash_multi_session_totals(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, store_id, _member_id, mgr_id = await _seed(db_session)
    cash = CashDrawerService(db_session)
    s1 = await cash.open_session(store_id, mgr_id, Decimal(1000))
    await cash.record_movement(store_id, CashMovementType.SALE_IN, Decimal(300))
    await cash.close_session(s1, counted_amount=Decimal(1300), closed_by=mgr_id)
    await cash.open_session(store_id, mgr_id, Decimal(500))
    await cash.record_movement(store_id, CashMovementType.SALE_IN, Decimal(200))
    day = _today_iso(s1)

    body = (
        await client.get("/api/v1/reports/daily-cash", params={"date": day}, headers=_auth(mgr))
    ).json()
    assert len(body["sessions"]) == 2
    assert body["total_opening_float"] == "1500"  # 1000 + 500
    assert body["total_cash_sales"] == "500"  # 300 + 200
    # 合計 expected = (1000+300) + (500+200) = 2000
    assert body["total_expected"] == "2000"
    # counted 僅含已關帳 s1（1300）；s2 未關帳不計
    assert body["total_counted"] == "1300"


async def test_daily_cash_store_credit_redeemed_is_display_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """購物金兌付：列 store_credit_redeemed_display_only，但不產生 cash movement、不進 expected。"""
    mgr, _clerk, store_id, member_id, mgr_id = await _seed(db_session)
    cash = CashDrawerService(db_session)
    session = await cash.open_session(store_id, mgr_id, Decimal(1000))
    sc = StoreCreditService(db_session)
    await sc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(500),
        premium_rate=Decimal(0),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=1,
        created_by=mgr_id,
    )
    await sc.debit(
        store_id,
        member_id,
        amount=Decimal(200),
        source_type=StoreCreditSourceType.SALE,
        source_id=1,
        created_by=mgr_id,
    )
    day = _today_iso(session)
    body = (
        await client.get("/api/v1/reports/daily-cash", params={"date": day}, headers=_auth(mgr))
    ).json()
    # 購物金兌付 200 只展示
    assert body["total_store_credit_redeemed_display_only"] == "200"
    # 不影響現金：cash_sales 與 expected 只反映開帳零用金
    assert body["total_cash_sales"] == "0"
    assert body["sessions"][0]["expected_amount"] == "1000"


async def test_daily_cash_is_read_only(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _clerk, store_id, _member_id, mgr_id = await _seed(db_session)
    cash = CashDrawerService(db_session)
    session = await cash.open_session(store_id, mgr_id, Decimal(1000))
    await cash.record_movement(store_id, CashMovementType.SALE_IN, Decimal(500))
    day = _today_iso(session)

    before_sessions = await db_session.scalar(select(func.count()).select_from(CashSession))
    before_moves = await db_session.scalar(select(func.count()).select_from(CashMovement))
    await client.get("/api/v1/reports/daily-cash", params={"date": day}, headers=_auth(mgr))
    after_sessions = await db_session.scalar(select(func.count()).select_from(CashSession))
    after_moves = await db_session.scalar(select(func.count()).select_from(CashMovement))
    assert before_sessions == after_sessions
    assert before_moves == after_moves


async def test_daily_cash_manager_only(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    _mgr, clerk, _store_id, _member_id, _mgr_id = await _seed(db_session)
    resp = await client.get(
        "/api/v1/reports/daily-cash", params={"date": "2026-06-20"}, headers=_auth(clerk)
    )
    assert resp.status_code == 403


async def test_daily_cash_csv_and_xlsx_export(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, store_id, _member_id, mgr_id = await _seed(db_session)
    cash = CashDrawerService(db_session)
    session = await cash.open_session(store_id, mgr_id, Decimal(1000))
    session.opened_at = datetime(2026, 7, 21, 16, 30, tzinfo=UTC)
    await cash.record_movement(store_id, CashMovementType.SALE_IN, Decimal(500))
    day = "2026-07-22"

    csv_resp = await client.get(
        "/api/v1/reports/daily-cash",
        params={"date": day, "format": "csv"},
        headers=_auth(mgr),
    )
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    text = csv_resp.content.decode("utf-8-sig")
    assert "應有現金" in text and "現金銷售" in text and "500" in text
    assert "2026-07-22T00:30:00+08:00" in text

    xlsx_resp = await client.get(
        "/api/v1/reports/daily-cash",
        params={"date": day, "format": "xlsx"},
        headers=_auth(mgr),
    )
    assert xlsx_resp.status_code == 200
    assert xlsx_resp.content[:2] == b"PK"
