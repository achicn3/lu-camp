"""SC-4 購物金報表 API 整合測試（docs/16 §4/§5A）：負債/帳齡、流量、對帳、匯出、權限。"""

from collections.abc import AsyncGenerator
from decimal import Decimal

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.contacts.models import Contact
from app.modules.store.models import Store
from app.modules.storecredit.service import StoreCreditService
from app.modules.user.models import User
from app.shared.enums import StoreCreditSourceType, UserRole


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
    """建店＋MANAGER＋CLERK＋會員，回 (mgr_token, clerk_token, store_id, member_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    member = Contact(store_id=store.id, name="會員甲", roles=["MEMBER"])
    session.add_all([mgr, clerk, member])
    await session.flush()
    mgr_token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
    clerk_token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return mgr_token, clerk_token, store.id, member.id, mgr.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_liability_report(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _clerk, store_id, member_id, mgr_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(500),
        premium_rate=Decimal(0),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=1,
        created_by=mgr_id,
    )
    resp = await client.get("/api/v1/reports/store-credit/liability", headers=_auth(mgr))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_outstanding"] == "500"
    # 剛入帳 → 帳齡落 <30 天
    assert body["aging_buckets"]["lt_30d"] == "500"
    assert body["aging_buckets"]["gt_365d"] == "0"
    assert len(body["per_member"]) == 1
    assert body["per_member"][0]["name"] == "會員甲"
    assert body["per_member"][0]["balance"] == "500"
    # 健康比分母（SC-5）未上線 → null
    assert body["liability_health_ratio"] is None


async def test_flows_report(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _clerk, store_id, member_id, mgr_id = await _seed(db_session)
    svc = StoreCreditService(db_session)
    await svc.credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(500),
        premium_rate=Decimal(0),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=1,
        created_by=mgr_id,
    )
    await svc.debit(
        store_id,
        member_id,
        amount=Decimal(200),
        source_type=StoreCreditSourceType.SALE,
        source_id=1,
        created_by=mgr_id,
    )
    resp = await client.get(
        "/api/v1/reports/store-credit/flows",
        params={
            "from": "2000-01-01T00:00:00Z",
            "to": "2100-01-01T00:00:00Z",
            "granularity": "month",
        },
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["issued"] == "500"
    assert rows[0]["redeemed"] == "200"
    assert rows[0]["net_change"] == "300"


async def test_flows_rejects_bad_range_and_granularity(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, _clerk, _store_id, _member_id, _mgr_id = await _seed(db_session)
    # to <= from
    bad = await client.get(
        "/api/v1/reports/store-credit/flows",
        params={"from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        headers=_auth(mgr),
    )
    assert bad.status_code == 422
    # 非法 granularity 由 Query Literal 擋（422）
    badg = await client.get(
        "/api/v1/reports/store-credit/flows",
        params={
            "from": "2026-01-01T00:00:00Z",
            "to": "2026-02-01T00:00:00Z",
            "granularity": "year",
        },
        headers=_auth(mgr),
    )
    assert badg.status_code == 422


async def test_reconciliation_report(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _clerk, store_id, member_id, mgr_id = await _seed(db_session)
    await StoreCreditService(db_session).credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(500),
        premium_rate=Decimal(0),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=1,
        created_by=mgr_id,
    )
    resp = await client.get("/api/v1/reports/store-credit/reconciliation", headers=_auth(mgr))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cached_total_trustworthy"] is True
    assert body["mismatches"] == []
    assert body["ledger_total_outstanding"] == "500"


async def test_csv_and_xlsx_export(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    mgr, _clerk, store_id, member_id, mgr_id = await _seed(db_session)
    await StoreCreditService(db_session).credit(
        store_id,
        member_id,
        cash_equivalent=Decimal(500),
        premium_rate=Decimal(0),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=1,
        created_by=mgr_id,
    )
    csv_resp = await client.get(
        "/api/v1/reports/store-credit/liability",
        params={"format": "csv"},
        headers=_auth(mgr),
    )
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "attachment" in csv_resp.headers["content-disposition"]
    text = csv_resp.content.decode("utf-8-sig")
    assert "會員甲" in text and "500" in text and "產生時間" in text

    xlsx_resp = await client.get(
        "/api/v1/reports/store-credit/liability",
        params={"format": "xlsx"},
        headers=_auth(mgr),
    )
    assert xlsx_resp.status_code == 200
    assert "spreadsheetml" in xlsx_resp.headers["content-type"]
    assert xlsx_resp.content[:2] == b"PK"  # xlsx 為 zip 容器


async def test_export_escapes_formula_injection(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """匯出防公式注入（Codex SC-4 P2）：以 = 開頭的會員姓名在 CSV 被前綴單引號。"""
    mgr, _clerk, store_id, _member_id, mgr_id = await _seed(db_session)
    evil = Contact(store_id=store_id, name="=cmd|' /C calc'!A1", roles=["MEMBER"])
    db_session.add(evil)
    await db_session.flush()
    await StoreCreditService(db_session).credit(
        store_id,
        evil.id,
        cash_equivalent=Decimal(100),
        premium_rate=Decimal(0),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=2,
        created_by=mgr_id,
    )
    resp = await client.get(
        "/api/v1/reports/store-credit/liability",
        params={"format": "csv"},
        headers=_auth(mgr),
    )
    text = resp.content.decode("utf-8-sig")
    assert "'=cmd" in text  # 危險開頭值已前綴單引號


async def test_reports_are_manager_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _mgr, clerk, _store_id, _member_id, _mgr_id = await _seed(db_session)
    resp = await client.get("/api/v1/reports/store-credit/liability", headers=_auth(clerk))
    assert resp.status_code == 403
