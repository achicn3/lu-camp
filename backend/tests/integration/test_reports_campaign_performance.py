"""C4 活動成效報表整合測試（docs/21 C4）：

每檔生效中/已結束活動的營運成效（區間 [starts_at, ends_at)，與 R2 同源）+ 該活動實際發出的折讓
（依 sale_line.campaign_id 精確歸屬）；DRAFT 不列；作廢不計；唯讀；MANAGER；CSV 匯出。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.service import CashDrawerService
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


def _auth(token: str, *, idem: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if idem is not None:
        headers["Idempotency-Key"] = idem
    return headers


async def _seed(session: AsyncSession) -> tuple[str, str, int, int]:
    """建店＋MANAGER＋CLERK（開帳）；回 (mgr_token, clerk_token, store_id, clerk_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add_all([mgr, clerk])
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal(1000))
    return (
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        store.id,
        clerk.id,
    )


async def _active_campaign(
    session: AsyncSession, store_id: int, clerk_id: int, *, pct: int = 10
) -> tuple[int, datetime, datetime]:
    """建立並啟用一檔涵蓋現在的活動（自有序號適用）；回 (id, starts_at, ends_at)。"""
    now = datetime.now(UTC)
    starts, ends = now - timedelta(days=1), now + timedelta(days=1)
    svc = CampaignService(session)
    c = await svc.create_campaign(
        store_id,
        name="開幕活動",
        discount_pct=pct,
        starts_at=starts,
        ends_at=ends,
        applies_owned_serialized=True,
        applies_owned_bulk=True,
        applies_catalog=False,
        applies_consignment=False,
        created_by=clerk_id,
    )
    await svc.activate(store_id, c.id, actor_user_id=clerk_id)
    return c.id, starts, ends


async def _owned(session: AsyncSession, store_id: int, *, code: str, cost: str, price: str) -> None:
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


async def _sell(client: httpx.AsyncClient, token: str, code: str, *, key: str) -> dict[str, object]:
    resp = await client.post(
        "/api/v1/sales",
        json={"lines": [{"line_type": "SERIALIZED", "item_code": code}]},
        headers=_auth(token, idem=key),
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, object] = resp.json()
    return body


async def _perf(client: httpx.AsyncClient, mgr: str) -> dict[str, object]:
    resp = await client.get("/api/v1/reports/campaign-performance", headers=_auth(mgr))
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    return body


async def test_campaign_performance_basic_and_same_source(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """活動九折賣自有 500→450：折讓 50、營業額/認列 450、毛利 150（450−300）；與 R2 同源。"""
    mgr, clerk, store_id, clerk_id = await _seed(db_session)
    cid, starts, ends = await _active_campaign(db_session, store_id, clerk_id, pct=10)
    await _owned(db_session, store_id, code="OWN-1", cost="300", price="500")
    await _sell(client, clerk, "OWN-1", key="p1")

    body = await _perf(client, mgr)
    rows = body["rows"]
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict)
    assert row["campaign_id"] == cid
    assert row["status"] == "ACTIVE"
    assert row["discount_pct"] == 10
    assert row["campaign_discount_total"] == "50"  # 500 − 450
    assert row["gross_turnover"] == "450"  # 折後成交
    assert row["recognized_revenue"] == "450"
    assert row["gross_margin"] == "150"  # 450 − 300
    assert row["transaction_count"] == 1

    # 與 R2 sales-margin 同源：以活動區間查毛利，數字應一致。
    margin = await client.get(
        "/api/v1/reports/sales-margin",
        params={"from": starts.isoformat(), "to": ends.isoformat()},
        headers=_auth(mgr),
    )
    assert margin.status_code == 200
    m = margin.json()
    assert row["gross_turnover"] == m["gross_turnover"]
    assert row["gross_margin"] == m["gross_margin"]
    assert row["recognized_revenue"] == m["recognized_revenue"]


async def test_voided_sale_excluded(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """作廢銷售不計入成效，也不計入活動折讓。"""
    mgr, clerk, store_id, clerk_id = await _seed(db_session)
    await _active_campaign(db_session, store_id, clerk_id, pct=10)
    await _owned(db_session, store_id, code="OWN-V", cost="300", price="500")
    sale = await _sell(client, clerk, "OWN-V", key="v1")
    voided = await client.post(f"/api/v1/sales/{sale['id']}/void", headers=_auth(mgr))
    assert voided.status_code == 200, voided.text

    rows = (await _perf(client, mgr))["rows"]
    assert isinstance(rows, list)
    row = rows[0]
    assert isinstance(row, dict)
    assert row["campaign_discount_total"] == "0"
    assert row["gross_turnover"] == "0"
    assert row["transaction_count"] == 0


async def test_draft_campaign_not_listed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """DRAFT（未啟用）活動無成交、不列入成效報表。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    now = datetime.now(UTC)
    await CampaignService(db_session).create_campaign(
        store_id,
        name="草稿活動",
        discount_pct=10,
        starts_at=now - timedelta(days=1),
        ends_at=now + timedelta(days=1),
        applies_owned_serialized=True,
        applies_owned_bulk=True,
        applies_catalog=False,
        applies_consignment=False,
        created_by=clerk_id,
    )
    assert (await _perf(client, mgr))["rows"] == []


async def test_empty_when_no_campaigns(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store_id, _clerk_id = await _seed(db_session)
    body = await _perf(client, mgr)
    assert body["rows"] == []


async def test_read_only_and_rbac(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, clerk, store_id, clerk_id = await _seed(db_session)
    await _active_campaign(db_session, store_id, clerk_id, pct=10)
    await _owned(db_session, store_id, code="OWN-R", cost="300", price="500")
    await _sell(client, clerk, "OWN-R", key="r1")

    before = await db_session.scalar(select(func.count()).select_from(Sale))
    await _perf(client, mgr)
    after = await db_session.scalar(select(func.count()).select_from(Sale))
    assert before == after  # 唯讀

    forbidden = await client.get("/api/v1/reports/campaign-performance", headers=_auth(clerk))
    assert forbidden.status_code == 403  # CLERK 不可


async def test_csv_export(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, clerk, store_id, clerk_id = await _seed(db_session)
    await _active_campaign(db_session, store_id, clerk_id, pct=10)
    await _owned(db_session, store_id, code="OWN-C", cost="300", price="500")
    await _sell(client, clerk, "OWN-C", key="c1")

    resp = await client.get(
        "/api/v1/reports/campaign-performance",
        params={"format": "csv"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 200
    text = resp.content.decode("utf-8-sig")
    assert "活動折讓總額" in text and "毛利" in text and "開幕活動" in text
