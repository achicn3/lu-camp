"""發票月報（申報用）：依期間列出銷項、作廢、折讓、進項，與尚未完成的發票。

US-068：月底會計要逐筆核對並產出申報資料。原本系統只有「發票待處理」頁，
沒有任何依期間匯出的清單——每個月都得自己翻資料庫。
"""

from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.einvoice.models import Invoice, InvoiceAllowance
from app.modules.purchasing.models import GoodsReceipt, PurchaseOrder, Supplier
from app.modules.sales.models import Sale
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    InvoiceStatus,
    InvoiceType,
    InvoiceVoidReason,
    PurchaseOrderStatus,
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


async def _seed(session: AsyncSession) -> tuple[str, str, int, int]:
    """建店＋店長/店員，回 (manager_token, clerk_token, store_id, clerk_id)。"""
    store = Store(name="發票月報店")
    session.add(store)
    await session.flush()
    mgr = User(store_id=store.id, username="ir-mgr", password_hash="h", role=UserRole.MANAGER)
    clerk = User(store_id=store.id, username="ir-clk", password_hash="h", role=UserRole.CLERK)
    session.add_all([mgr, clerk])
    await session.flush()
    return (
        encode_access_token(user_id=mgr.id, role="MANAGER", store_id=store.id),
        encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
        store.id,
        clerk.id,
    )


async def _sale(
    session: AsyncSession, store_id: int, clerk_id: int, *, total: str, when: datetime
) -> int:
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
    return sale.id


async def _invoice(
    session: AsyncSession,
    store_id: int,
    sale_id: int,
    *,
    no: str,
    when: date,
    total: str,
    status: InvoiceStatus = InvoiceStatus.ISSUED,
    void_reason: InvoiceVoidReason | None = None,
    buyer_tax_id: str | None = None,
) -> int:
    net = Decimal(total) - Decimal(total) * Decimal("0.05") / Decimal("1.05")
    net_i = Decimal(int(net))
    invoice = Invoice(
        store_id=store_id,
        sale_id=sale_id,
        invoice_type=InvoiceType.B2B if buyer_tax_id else InvoiceType.B2C,
        invoice_no=no,
        invoice_date=when,
        buyer_tax_id=buyer_tax_id,
        status=status,
        void_reason=void_reason,
        net=net_i,
        tax=Decimal(total) - net_i,
        total=Decimal(total),
    )
    session.add(invoice)
    await session.flush()
    return invoice.id


async def test_invoice_register_lists_the_period_by_category(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """銷項、作廢、折讓、進項各自列出，並附原交易識別資訊。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    inside = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    outside = datetime(2026, 8, 10, 6, 0, tzinfo=UTC)

    issued_sale = await _sale(db_session, store_id, clerk_id, total="1050", when=inside)
    await _invoice(
        db_session, store_id, issued_sale, no="AA10000001", when=date(2026, 9, 10), total="1050"
    )
    b2b_sale = await _sale(db_session, store_id, clerk_id, total="2100", when=inside)
    await _invoice(
        db_session,
        store_id,
        b2b_sale,
        no="AA10000002",
        when=date(2026, 9, 11),
        total="2100",
        buyer_tax_id="12345678",
    )
    voided_sale = await _sale(db_session, store_id, clerk_id, total="500", when=inside)
    await _invoice(
        db_session,
        store_id,
        voided_sale,
        no="AA10000003",
        when=date(2026, 9, 12),
        total="500",
        status=InvoiceStatus.VOID,
        void_reason=InvoiceVoidReason.SALE_VOID,
    )
    allowance_sale = await _sale(db_session, store_id, clerk_id, total="800", when=inside)
    allowance_invoice = await _invoice(
        db_session, store_id, allowance_sale, no="AA10000004", when=date(2026, 9, 13), total="800"
    )
    db_session.add(
        InvoiceAllowance(
            store_id=store_id,
            invoice_id=allowance_invoice,
            allowance_no="DD10000001",
            net=Decimal(190),
            tax=Decimal(10),
            total=Decimal(200),
            created_at=inside,
        )
    )
    # 期間外的發票不能混進來
    old_sale = await _sale(db_session, store_id, clerk_id, total="999", when=outside)
    await _invoice(
        db_session, store_id, old_sale, no="AA09999999", when=date(2026, 8, 10), total="999"
    )

    # 進項：收貨時登記的供應商發票
    supplier = Supplier(store_id=store_id, name="裝備大盤商")
    db_session.add(supplier)
    await db_session.flush()
    po = PurchaseOrder(
        store_id=store_id,
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        status=PurchaseOrderStatus.RECEIVED,
        ordered_by=clerk_id,
    )
    db_session.add(po)
    await db_session.flush()
    db_session.add(
        GoodsReceipt(
            store_id=store_id,
            purchase_order_id=po.id,
            received_by=clerk_id,
            invoice_number="BB20000001",
            invoice_date=date(2026, 9, 9),
            invoice_net=Decimal(1000),
            invoice_tax=Decimal(50),
            invoice_total=Decimal(1050),
            received_at=inside,
        )
    )
    await db_session.flush()

    resp = await client.get(
        "/api/v1/reports/invoice-register",
        params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    issued = {row["number"] for row in body["issued"]}
    # 有折讓的那張（AA10000004）**仍是銷項**——折讓是另一筆、另計，不會把原發票抽掉。
    assert issued == {"AA10000001", "AA10000002", "AA10000004"}  # 作廢的不算、期間外的不進來
    b2b = next(row for row in body["issued"] if row["number"] == "AA10000002")
    assert b2b["buyer_tax_id"] == "12345678"
    assert b2b["sale_id"] == b2b_sale

    assert [row["number"] for row in body["voided"]] == ["AA10000003"]
    assert body["voided"][0]["void_reason"] == "SALE_VOID"

    assert [row["number"] for row in body["allowances"]] == ["DD10000001"]
    assert body["allowances"][0]["invoice_no"] == "AA10000004"
    assert body["allowances"][0]["total"] == "200"

    assert [row["number"] for row in body["input_invoices"]] == ["BB20000001"]
    assert body["input_invoices"][0]["counterparty"] == "裝備大盤商"

    # 合計要能與畫面核對
    assert body["totals"]["issued_total"] == "3950"  # 1050 + 2100 + 800（折讓那張仍是銷項）
    assert body["totals"]["voided_total"] == "500"
    assert body["totals"]["allowance_total"] == "200"
    assert body["totals"]["input_total"] == "1050"


async def test_invoice_register_lists_unfinished_invoices_separately(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """待開立／平台退回的另列——申報前要先清掉，不能混進銷項總額。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    when = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    pending_sale = await _sale(db_session, store_id, clerk_id, total="600", when=when)
    await _invoice(
        db_session,
        store_id,
        pending_sale,
        no="AA10000010",
        when=date(2026, 9, 10),
        total="600",
        status=InvoiceStatus.PENDING,
    )

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    assert [row["number"] for row in body["unfinished"]] == ["AA10000010"]
    assert body["unfinished"][0]["status"] == "PENDING"
    assert all(row["number"] != "AA10000010" for row in body["issued"])
    assert body["totals"]["issued_total"] == "0"


async def test_invoice_register_exports_csv_with_period_and_store(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """匯出檔要能直接交給會計：標明店別、期間、產生時間，每列附類別。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    when = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="1050", when=when)
    await _invoice(
        db_session, store_id, sale_id, no="AA10000020", when=date(2026, 9, 10), total="1050"
    )

    resp = await client.get(
        "/api/v1/reports/invoice-register",
        params={
            "from": "2026-09-01T00:00:00+08:00",
            "to": "2026-10-01T00:00:00+08:00",
            "format": "csv",
        },
        headers=_auth(mgr),
    )
    assert resp.status_code == 200, resp.text
    text = resp.content.decode("utf-8-sig")
    assert "銷項" in text and "AA10000020" in text
    assert "期間" in text and "店別" in text


async def test_invoice_register_is_manager_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    _mgr, clerk, _store_id, _clerk_id = await _seed(db_session)
    resp = await client.get(
        "/api/v1/reports/invoice-register",
        params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
        headers=_auth(clerk),
    )
    assert resp.status_code == 403
