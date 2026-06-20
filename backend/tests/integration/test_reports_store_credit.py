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
from app.modules.inventory.models import CatalogProduct
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
    member = Contact(
        store_id=store.id,
        name="會員甲",
        roles=["MEMBER", "SELLER"],
        national_id_enc="enc",
    )
    session.add_all([mgr, clerk, member])
    await session.flush()
    mgr_token = encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id)
    clerk_token = encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id)
    return mgr_token, clerk_token, store.id, member.id, mgr.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _auth_idem(token: str, key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Idempotency-Key": key}


async def _seed_catalog(session: AsyncSession, store_id: int, *, price: str, qty: int) -> int:
    product = CatalogProduct(
        store_id=store_id,
        sku=f"FLOW-SKU-{store_id}",
        name="報表測試商品",
        unit_price=Decimal(price),
        quantity_on_hand=qty,
    )
    session.add(product)
    await session.flush()
    return product.id


async def _create_store_credit_buyout(
    client: httpx.AsyncClient,
    token: str,
    contact_id: int,
    *,
    key: str,
    amount: str = "500",
) -> int:
    resp = await client.post(
        "/api/v1/acquisitions",
        json={
            "type": "BUYOUT",
            "contact_id": contact_id,
            "payout_method": "STORE_CREDIT",
            "items": [
                {
                    "name": "購物金報表測試品",
                    "grade": "A",
                    "acquisition_cost": amount,
                    "listed_price": amount,
                }
            ],
        },
        headers=_auth_idem(token, key),
    )
    assert resp.status_code == 201, resp.text
    return int(resp.json()["acquisition_id"])


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
    # monthly_fixed_cash_outflow 未設（0）→ 健康比 N/A（null）
    assert body["liability_health_ratio"] is None


async def test_liability_health_ratio_uses_monthly_outflow(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """SC-5a 接上：設了月固定現金支出後，負債健康比 = 總負債 ÷ 該值。"""
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
    patched = await client.patch(
        "/api/v1/settings",
        json={"monthly_fixed_cash_outflow": "1000"},
        headers=_auth(mgr),
    )
    assert patched.status_code == 200, patched.text
    body = (await client.get("/api/v1/reports/store-credit/liability", headers=_auth(mgr))).json()
    assert body["liability_health_ratio"] == "0.50"  # 500 / 1000


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
    # 無沖正 → gross == net、reversed == 0（R0 稽核分欄）
    assert rows[0]["issued_gross"] == "500"
    assert rows[0]["issued_reversed"] == "0"
    assert rows[0]["redeemed_gross"] == "200"
    assert rows[0]["redeemed_reversed"] == "0"


async def test_flows_report_nets_acquisition_rollback_reversal(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, store_id, member_id, _mgr_id = await _seed(db_session)
    acq_id = await _create_store_credit_buyout(client, clerk, member_id, key="flows-acq-rollback")
    credited = await StoreCreditService(db_session).get_balance(store_id, member_id)
    voided = await client.post(
        f"/api/v1/acquisitions/{acq_id}/void",
        json={"reason": "報表沖正測試"},
        headers=_auth(mgr),
    )
    assert voided.status_code == 200, voided.text

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
    assert rows[0]["issued"] == "0"
    assert rows[0]["redeemed"] == "0"
    assert rows[0]["net_change"] == "0"
    # 同期沖正：issued_gross 仍記錄原發出（含溢價）、issued_reversed 抵銷 → net 0（稽核可見毛額）
    assert rows[0]["issued_gross"] == str(credited)
    assert rows[0]["issued_reversed"] == str(credited)


async def test_flows_report_nets_sale_void_reversal(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    mgr, clerk, store_id, member_id, _mgr_id = await _seed(db_session)
    await _create_store_credit_buyout(client, clerk, member_id, key="flows-sale-credit")
    issued = await StoreCreditService(db_session).get_balance(store_id, member_id)
    catalog_id = await _seed_catalog(db_session, store_id, price="100", qty=10)
    sale = await client.post(
        "/api/v1/sales",
        json={
            "lines": [{"line_type": "CATALOG", "catalog_product_id": catalog_id, "qty": 2}],
            "buyer_contact_id": member_id,
            "tenders": [{"tender_type": "STORE_CREDIT", "amount": "200"}],
        },
        headers=_auth_idem(clerk, "flows-sale-void"),
    )
    assert sale.status_code == 201, sale.text
    sale_id = int(sale.json()["id"])
    voided = await client.post(
        f"/api/v1/sales/{sale_id}/void",
        headers=_auth(mgr),
    )
    assert voided.status_code == 200, voided.text

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
    assert rows[0]["issued"] == str(issued)
    assert rows[0]["redeemed"] == "0"
    assert rows[0]["net_change"] == str(issued)
    # 兌付後作廢：redeemed_gross 記原兌付、redeemed_reversed 抵銷 → redeemed_net 0
    assert rows[0]["redeemed_gross"] == "200"
    assert rows[0]["redeemed_reversed"] == "200"


async def test_flows_breakdown_cross_period_reconciles(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """跨期沖正（docs/19 §3.3）：gross 落發出月、reversed 落沖正月；各期 net 可解釋、
    全期 issued_net 合計 = 0。以 service 直接注入不同月份的帳本列驗證。"""
    mgr, _clerk, store_id, member_id, mgr_id = await _seed(db_session)
    from datetime import UTC, datetime

    from app.modules.storecredit.models import StoreCreditAccount, StoreCreditLedger
    from app.shared.enums import StoreCreditEntryType

    db_session.add(StoreCreditAccount(store_id=store_id, contact_id=member_id, balance=Decimal(0)))
    await db_session.flush()
    # 1 月發出 500（CREDIT）
    credit = StoreCreditLedger(
        store_id=store_id,
        contact_id=member_id,
        entry_type=StoreCreditEntryType.CREDIT,
        signed_amount=Decimal(500),
        balance_after=Decimal(500),
        cash_equivalent=Decimal(500),
        premium_rate_applied=Decimal(0),
        source_type=StoreCreditSourceType.ACQUISITION,
        source_id=1,
        fingerprint="x" * 64,
        created_by=mgr_id,
        created_at=datetime(2026, 1, 15, tzinfo=UTC),
    )
    db_session.add(credit)
    await db_session.flush()
    # 2 月沖正（ACQUISITION_ROLLBACK，signed = -500）
    db_session.add(
        StoreCreditLedger(
            store_id=store_id,
            contact_id=member_id,
            entry_type=StoreCreditEntryType.REVERSAL,
            signed_amount=Decimal(-500),
            balance_after=Decimal(0),
            source_type=StoreCreditSourceType.ACQUISITION_ROLLBACK,
            source_id=1,
            reversal_of_id=credit.id,
            fingerprint="y" * 64,
            created_by=mgr_id,
            created_at=datetime(2026, 2, 10, tzinfo=UTC),
        )
    )
    await db_session.flush()

    resp = await client.get(
        "/api/v1/reports/store-credit/flows",
        params={
            "from": "2026-01-01T00:00:00Z",
            "to": "2026-03-01T00:00:00Z",
            "granularity": "month",
        },
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()["rows"]
    assert len(rows) == 2
    jan, feb = rows[0], rows[1]
    # 1 月：毛額發出 500、net +500、未沖正
    assert jan["issued_gross"] == "500"
    assert jan["issued_reversed"] == "0"
    assert jan["issued"] == "500"
    # 2 月：發出毛額 0、沖正 500、net -500
    assert feb["issued_gross"] == "0"
    assert feb["issued_reversed"] == "500"
    assert feb["issued"] == "-500"
    # 全期 issued_net 合計 = 0（沖正後無有效負債）
    assert Decimal(jan["issued"]) + Decimal(feb["issued"]) == Decimal(0)


async def test_flows_net_change_includes_manual_adjustment(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """R0 review F1（docs/19 §3.1）：net_change 須含人工 ADJUSTMENT，恰等於該期帳本 signed
    淨變化、可與 liability 餘額對上。issued/redeemed 仍只計發出/兌付，adjustment 另列。"""
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
    # 人工校正 -100（MANAGER 校正）
    await svc.adjust(
        store_id,
        member_id,
        amount=Decimal(-100),
        reason="盤點校正",
        created_by=mgr_id,
        idempotency_key="adj-flows-1",
    )
    balance = await svc.get_balance(store_id, member_id)
    assert balance == Decimal(400)  # 500 - 100

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
    row = rows[0]
    # 發出/兌付不含 adjustment；adjustment 另列；net_change 含之
    assert row["issued"] == "500"
    assert row["redeemed"] == "0"
    assert row["adjustment_net"] == "-100"
    assert row["net_change"] == "400"
    # net_change 對上帳本淨變化（= 當前餘額，本店唯一會員、單期）
    assert Decimal(row["net_change"]) == balance


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


async def test_flows_csv_carries_gross_reversed_columns(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """R0：flows CSV 須含 gross/reversed 稽核欄，與 JSON 同源。"""
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
    resp = await client.get(
        "/api/v1/reports/store-credit/flows",
        params={
            "from": "2000-01-01T00:00:00Z",
            "to": "2100-01-01T00:00:00Z",
            "granularity": "month",
            "format": "csv",
        },
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    text = resp.content.decode("utf-8-sig")
    assert "發出毛額" in text and "發出沖正" in text
    assert "兌付毛額" in text and "兌付沖正" in text
    assert "人工調整" in text


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
