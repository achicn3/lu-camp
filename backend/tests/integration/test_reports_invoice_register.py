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

from app.core.audit import AuditLog
from app.core.db import get_session
from app.core.security import encode_access_token
from app.main import create_app
from app.modules.einvoice.models import EInvoiceUploadQueue, Invoice, InvoiceAllowance
from app.modules.purchasing.models import GoodsReceipt, PurchaseOrder, Supplier
from app.modules.returns.models import CustomerReturn, ReturnLine
from app.modules.sales.models import Sale, SaleLine
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    EInvoiceAction,
    EInvoiceIssueChannel,
    InvoiceStatus,
    InvoiceType,
    InvoiceVoidReason,
    PurchaseOrderStatus,
    SaleLineType,
    SaleStatus,
    UploadStatus,
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
    """建店＋店長/店員，回 (manager_token, clerk_token, store_id, clerk_id)。

    帳號名帶店 id：跨店測試會建第二家店，username 全庫唯一。
    """
    store = Store(name="發票月報店")
    session.add(store)
    await session.flush()
    mgr = User(
        store_id=store.id, username=f"ir-mgr-{store.id}", password_hash="h", role=UserRole.MANAGER
    )
    clerk = User(
        store_id=store.id, username=f"ir-clk-{store.id}", password_hash="h", role=UserRole.CLERK
    )
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
    allowance = InvoiceAllowance(
        store_id=store_id,
        invoice_id=allowance_invoice,
        allowance_no="DD10000001",
        net=Decimal(190),
        tax=Decimal(10),
        total=Decimal(200),
        created_at=inside,
    )
    db_session.add(allowance)
    await db_session.flush()
    # 折讓要平台核可才算數（見另一支測試）：這筆已 UPLOADED。
    db_session.add(
        EInvoiceUploadQueue(
            store_id=store_id,
            action=EInvoiceAction.ALLOWANCE,
            message_type="G0401",
            allowance_id=allowance.id,
            status=UploadStatus.UPLOADED,
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
    totals = body["totals"]
    assert totals["issued_total"] == "3950"  # 1050 + 2100 + 800（折讓那張仍是銷項）
    assert totals["voided_total"] == "500"
    assert totals["allowance_total"] == "200"
    assert totals["input_total"] == "1050"
    # **稅額也要守住**：申報書填的是未稅銷售額與稅額，合計算錯或接錯欄位不能只靠人眼看。
    assert totals["issued_net"] == "3761"  # 1000 + 2000 + 761（fixture 的未稅取整無條件捨去）
    assert totals["issued_tax"] == "189"  # 50 + 100 + 39
    assert int(totals["issued_net"]) + int(totals["issued_tax"]) == int(totals["issued_total"])
    assert totals["allowance_tax"] == "10"
    assert totals["input_tax"] == "50"
    assert totals["manual_paper_refund_total"] == "0"  # 本期沒有紙本待調整


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
    # 畫面上有的合計，匯出檔就要有——會計拿到的是這個檔（US-068）。
    for label in ("銷項合計", "銷項稅額", "折讓稅額", "進項稅額", "手開紙本待調整退款"):
        assert label in text, label


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


async def test_invoice_register_uses_taiwan_calendar_dates(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """期間是**台灣日曆日**：9 月的月報不能混進 8/31、也不能漏掉 9/30。

    from/to 進到後端是 UTC（9/1 00:00+08 ＝ 8/31 16:00Z）。直接對 UTC 取 .date()
    會把界線整個往前挪一天——申報數字就從第一步錯起。
    """
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    when = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    aug31 = await _sale(db_session, store_id, clerk_id, total="100", when=when)
    await _invoice(
        db_session, store_id, aug31, no="AA20260831", when=date(2026, 8, 31), total="100"
    )
    sep30 = await _sale(db_session, store_id, clerk_id, total="200", when=when)
    await _invoice(
        db_session, store_id, sep30, no="AA20260930", when=date(2026, 9, 30), total="200"
    )

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    numbers = {row["number"] for row in body["issued"]}
    assert "AA20260831" not in numbers  # 8/31 是上個月
    assert "AA20260930" in numbers  # 9/30 仍在本月


async def test_invoice_register_keeps_invoices_whose_void_is_unconfirmed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """作廢待平台確認（VOID_PENDING）之前，那張發票**仍然有效**（ADR-019）。

    先從銷項拿掉會低報營業額；但也不能不提醒，所以另外列進「未完成」。
    """
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    when = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="1050", when=when)
    await _invoice(
        db_session,
        store_id,
        sale_id,
        no="AA10000030",
        when=date(2026, 9, 10),
        total="1050",
        status=InvoiceStatus.VOID_PENDING,
        void_reason=InvoiceVoidReason.SALE_VOID,  # 已申請作廢，平台尚未確認
    )

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    assert [row["number"] for row in body["issued"]] == ["AA10000030"]
    assert body["totals"]["issued_total"] == "1050"  # 平台確認作廢前仍是銷項
    assert body["totals"]["voided_total"] == "0"
    assert [row["number"] for row in body["unfinished"]] == ["AA10000030"]  # 但要提醒去收尾


async def test_invoice_register_excludes_drafts_that_were_never_issued(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """開立前就被作廢的（沒有號碼）不是「作廢發票」——平台上從來沒有那張票。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    when = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="700", when=when)
    invoice = Invoice(
        store_id=store_id,
        sale_id=sale_id,
        invoice_type=InvoiceType.B2C,
        invoice_no=None,  # 從未取得字軌號碼
        invoice_date=None,
        status=InvoiceStatus.VOID,
        void_reason=InvoiceVoidReason.SALE_VOID,
        net=Decimal(667),
        tax=Decimal(33),
        total=Decimal(700),
        created_at=when,
    )
    db_session.add(invoice)
    await db_session.flush()

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    assert body["voided"] == []
    assert body["totals"]["voided_total"] == "0"
    assert len(body["unfinished"]) == 1  # 仍要看得到，但不是作廢稅單


async def test_invoice_register_separates_allowances_the_platform_has_not_accepted(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """折讓要平台核可（G0401 UPLOADED）才算數；待送或被退回的不能進申報合計。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    when = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="800", when=when)
    invoice_id = await _invoice(
        db_session, store_id, sale_id, no="AA10000040", when=date(2026, 9, 10), total="800"
    )
    accepted = InvoiceAllowance(
        store_id=store_id,
        invoice_id=invoice_id,
        allowance_no="DD10000010",
        net=Decimal(95),
        tax=Decimal(5),
        total=Decimal(100),
        created_at=when,
    )
    pending = InvoiceAllowance(
        store_id=store_id,
        invoice_id=invoice_id,
        allowance_no=None,
        net=Decimal(190),
        tax=Decimal(10),
        total=Decimal(200),
        created_at=when,
    )
    db_session.add_all([accepted, pending])
    await db_session.flush()
    db_session.add_all(
        [
            EInvoiceUploadQueue(
                store_id=store_id,
                action=EInvoiceAction.ALLOWANCE,
                message_type="G0401",
                allowance_id=accepted.id,
                status=UploadStatus.UPLOADED,
            ),
            EInvoiceUploadQueue(
                store_id=store_id,
                action=EInvoiceAction.ALLOWANCE,
                message_type="G0401",
                allowance_id=pending.id,
                status=UploadStatus.FAILED,
            ),
        ]
    )
    await db_session.flush()

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    assert [row["number"] for row in body["allowances"]] == ["DD10000010"]
    assert body["totals"]["allowance_total"] == "100"  # 被退回的 200 不算
    assert any(row["total"] == "200" for row in body["unfinished"])


async def test_invoice_register_flags_manual_paper_returns_for_manual_adjustment(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """手開紙本的單退貨後**不會**產生電子折讓（docs/36）——月報必須自己把它標出來。

    不標的話：整筆退掉的 1,050 元在月報上仍是 1,050 元銷項、折讓 0，會計照著申報就錯了，
    而且畫面上完全看不出哪裡要人工處理。
    """
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    when = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="1050", when=when)
    invoice = Invoice(
        store_id=store_id,
        sale_id=sale_id,
        invoice_type=InvoiceType.B2C,
        invoice_no="MP10000001",
        invoice_date=date(2026, 9, 10),
        status=InvoiceStatus.ISSUED,
        issue_channel=EInvoiceIssueChannel.MANUAL_PAPER,
        net=Decimal(1000),
        tax=Decimal(50),
        total=Decimal(1050),
    )
    db_session.add(invoice)
    await _return_one_line(db_session, store_id, sale_id, clerk_id, when=when)
    sale = await db_session.get(Sale, sale_id)
    assert sale is not None
    sale.status = SaleStatus.RETURNED  # 紙本依國稅局程序另行處理（本例只退其中 525）
    await db_session.flush()

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    row = body["manual_paper_adjustments"][0]
    assert [r["number"] for r in body["manual_paper_adjustments"]] == ["MP10000001"]
    # 退了多少要看得出來：這筆只退了 525（部分），不是整張 1,050
    assert row["status"] == "本期有退貨"
    assert "本期退款 525／發票 1050" in row["reference"]
    assert body["totals"]["manual_paper_refund_total"] == "525"
    # 發票本身仍是有效銷項（紙本處置是店家線下作業），但要有這張待辦清單
    assert [row["number"] for row in body["issued"]] == ["MP10000001"]


async def test_invoice_register_does_not_flag_normal_electronic_returns(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """電子發票的退貨有折讓／作廢流程接手，不該混進人工待辦清單。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    when = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="500", when=when)
    await _invoice(
        db_session, store_id, sale_id, no="AA10000050", when=date(2026, 9, 10), total="500"
    )
    await _return_one_line(db_session, store_id, sale_id, clerk_id, when=when)
    sale = await db_session.get(Sale, sale_id)
    assert sale is not None
    sale.status = SaleStatus.RETURNED
    await db_session.flush()

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    assert body["manual_paper_adjustments"] == []


async def _return_one_line(
    session: AsyncSession, store_id: int, sale_id: int, clerk_id: int, *, when: datetime
) -> None:
    """在 sale 上建一行並退掉一件（部分退貨：sale.status 仍是 COMPLETED）。"""
    line = SaleLine(
        store_id=store_id,
        sale_id=sale_id,
        line_type=SaleLineType.CATALOG,
        description="退一件",
        qty=2,
        unit_price=Decimal(525),
        line_total=Decimal(1050),
        net_amount=Decimal(1050),
    )
    session.add(line)
    await session.flush()
    customer_return = CustomerReturn(
        store_id=store_id,
        sale_id=sale_id,
        reason="部分退貨",
        clerk_user_id=clerk_id,
        refund_amount=Decimal(525),
        created_at=when,
    )
    session.add(customer_return)
    await session.flush()
    session.add(
        ReturnLine(
            store_id=store_id,
            return_id=customer_return.id,
            sale_line_id=line.id,
            qty=1,
            refund_amount=Decimal(525),
        )
    )
    await session.flush()


async def test_invoice_register_flags_partial_paper_returns(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """**部分**退貨時 sale 仍是 COMPLETED——不能只看銷售狀態，否則整批漏掉。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    when = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="1050", when=when)
    db_session.add(
        Invoice(
            store_id=store_id,
            sale_id=sale_id,
            invoice_type=InvoiceType.B2C,
            invoice_no="MP10000002",
            invoice_date=date(2026, 9, 10),
            status=InvoiceStatus.ISSUED,
            issue_channel=EInvoiceIssueChannel.MANUAL_PAPER,
            net=Decimal(1000),
            tax=Decimal(50),
            total=Decimal(1050),
        )
    )
    await _return_one_line(db_session, store_id, sale_id, clerk_id, when=when)

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    assert [row["number"] for row in body["manual_paper_adjustments"]] == ["MP10000002"]


async def test_invoice_register_flags_paper_returns_from_a_previous_month(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """8/31 開的紙本、9/10 才退——9 月的月報必須看得到這筆待調整。

    以開立日篩選的話，這張發票根本不在 9 月的清單裡，於是既沒折讓也沒有任何提示。
    """
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    issued_when = datetime(2026, 8, 31, 6, 0, tzinfo=UTC)
    returned_when = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="1050", when=issued_when)
    db_session.add(
        Invoice(
            store_id=store_id,
            sale_id=sale_id,
            invoice_type=InvoiceType.B2C,
            invoice_no="MP10000003",
            invoice_date=date(2026, 8, 31),
            status=InvoiceStatus.ISSUED,
            issue_channel=EInvoiceIssueChannel.MANUAL_PAPER,
            net=Decimal(1000),
            tax=Decimal(50),
            total=Decimal(1050),
            created_at=issued_when,
        )
    )
    await _return_one_line(db_session, store_id, sale_id, clerk_id, when=returned_when)

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    assert [row["number"] for row in body["manual_paper_adjustments"]] == ["MP10000003"]
    assert [row["number"] for row in body["issued"]] == []  # 8 月開的票不算 9 月銷項


async def test_invoice_register_export_includes_paper_adjustments(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """匯出檔也要有這個警示——會計拿到的是下載檔，不是畫面。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    when = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="1050", when=when)
    db_session.add(
        Invoice(
            store_id=store_id,
            sale_id=sale_id,
            invoice_type=InvoiceType.B2C,
            invoice_no="MP10000004",
            invoice_date=date(2026, 9, 10),
            status=InvoiceStatus.ISSUED,
            issue_channel=EInvoiceIssueChannel.MANUAL_PAPER,
            net=Decimal(1000),
            tax=Decimal(50),
            total=Decimal(1050),
        )
    )
    await _return_one_line(db_session, store_id, sale_id, clerk_id, when=when)

    resp = await client.get(
        "/api/v1/reports/invoice-register",
        params={
            "from": "2026-09-01T00:00:00+08:00",
            "to": "2026-10-01T00:00:00+08:00",
            "format": "csv",
        },
        headers=_auth(mgr),
    )
    text = resp.content.decode("utf-8-sig")
    assert "手開紙本待調整" in text
    assert text.count("MP10000004") >= 2  # 銷項一列、待調整一列


async def _input_invoice(
    session: AsyncSession,
    store_id: int,
    clerk_id: int,
    *,
    number: str,
    when: date,
    total: str,
) -> None:
    """建一張進項發票（供應商＋採購單＋收貨批次）。"""
    supplier = Supplier(store_id=store_id, name=f"供應商{number}")
    session.add(supplier)
    await session.flush()
    po = PurchaseOrder(
        store_id=store_id,
        supplier_id=supplier.id,
        supplier_name=supplier.name,
        status=PurchaseOrderStatus.RECEIVED,
        ordered_by=clerk_id,
    )
    session.add(po)
    await session.flush()
    net = Decimal(int(Decimal(total) / Decimal("1.05")))
    session.add(
        GoodsReceipt(
            store_id=store_id,
            purchase_order_id=po.id,
            received_by=clerk_id,
            invoice_number=number,
            invoice_date=when,
            invoice_net=net,
            invoice_tax=Decimal(total) - net,
            invoice_total=Decimal(total),
        )
    )
    await session.flush()


async def test_invoice_register_covers_the_whole_last_day_even_if_to_is_23_59(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """`to` 寫成 9/30 23:59:59（很自然的月底寫法）時，9/30 整天仍要算進來。

    只用「台北日 <」當界線的話，這一整天的銷項與進項會無聲消失——而這支端點的輸出
    是拿去申報的。
    """
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    when = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="300", when=when)
    await _invoice(
        db_session, store_id, sale_id, no="AA20260930", when=date(2026, 9, 30), total="300"
    )
    await _input_invoice(
        db_session, store_id, clerk_id, number="BB20260930", when=date(2026, 9, 30), total="105"
    )

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-09-30T23:59:59+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    assert [row["number"] for row in body["issued"]] == ["AA20260930"]
    assert [row["number"] for row in body["input_invoices"]] == ["BB20260930"]


async def test_invoice_register_input_invoices_respect_taiwan_day_boundaries(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """進項的期間界線也要用台北日（原本只有銷項有測試，進項改壞了不會紅）。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    await _input_invoice(
        db_session, store_id, clerk_id, number="BB20260831", when=date(2026, 8, 31), total="105"
    )
    await _input_invoice(
        db_session, store_id, clerk_id, number="BB20260930", when=date(2026, 9, 30), total="210"
    )

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    numbers = {row["number"] for row in body["input_invoices"]}
    assert numbers == {"BB20260930"}  # 8/31 是上個月


async def test_invoice_register_allowance_period_uses_taiwan_time(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """折讓依建立時間歸期：台北 9/30 23:50（＝ UTC 15:50）仍屬 9 月。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    sale_id = await _sale(
        db_session, store_id, clerk_id, total="800", when=datetime(2026, 9, 20, 6, tzinfo=UTC)
    )
    invoice_id = await _invoice(
        db_session, store_id, sale_id, no="AA10000060", when=date(2026, 9, 20), total="800"
    )
    late = InvoiceAllowance(
        store_id=store_id,
        invoice_id=invoice_id,
        allowance_no="DD20260930",
        net=Decimal(95),
        tax=Decimal(5),
        total=Decimal(100),
        created_at=datetime(2026, 9, 30, 15, 50, tzinfo=UTC),  # 台北 9/30 23:50
    )
    db_session.add(late)
    await db_session.flush()
    db_session.add(
        EInvoiceUploadQueue(
            store_id=store_id,
            action=EInvoiceAction.ALLOWANCE,
            message_type="G0401",
            allowance_id=late.id,
            status=UploadStatus.UPLOADED,
        )
    )
    await db_session.flush()

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    assert [row["number"] for row in body["allowances"]] == ["DD20260930"]
    assert body["allowances"][0]["issued_on"] == "2026-09-30"  # 顯示日期也要是台北日


async def test_invoice_register_never_leaks_another_store(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """§4 店別範圍：別家店的發票、折讓、進項、紙本待調整都不得出現在本店月報。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    other_mgr, _other_clerk, other_store, other_clerk = await _seed(db_session)
    when = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)

    mine = await _sale(db_session, store_id, clerk_id, total="1050", when=when)
    await _invoice(
        db_session, store_id, mine, no="AA10000070", when=date(2026, 9, 10), total="1050"
    )
    await _input_invoice(
        db_session, store_id, clerk_id, number="BB10000070", when=date(2026, 9, 10), total="105"
    )

    theirs = await _sale(db_session, other_store, other_clerk, total="9999", when=when)
    await _invoice(
        db_session, other_store, theirs, no="ZZ99999999", when=date(2026, 9, 10), total="9999"
    )
    await _input_invoice(
        db_session,
        other_store,
        other_clerk,
        number="ZZ88888888",
        when=date(2026, 9, 10),
        total="210",
    )
    paper_sale = await _sale(db_session, other_store, other_clerk, total="1050", when=when)
    db_session.add(
        Invoice(
            store_id=other_store,
            sale_id=paper_sale,
            invoice_type=InvoiceType.B2C,
            invoice_no="ZZ77777777",
            invoice_date=date(2026, 9, 10),
            status=InvoiceStatus.ISSUED,
            issue_channel=EInvoiceIssueChannel.MANUAL_PAPER,
            net=Decimal(1000),
            tax=Decimal(50),
            total=Decimal(1050),
        )
    )
    await _return_one_line(db_session, other_store, paper_sale, other_clerk, when=when)

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    every_number = {
        row["number"]
        for key in (
            "issued",
            "voided",
            "allowances",
            "input_invoices",
            "unfinished",
            "manual_paper_adjustments",
        )
        for row in body[key]
    }
    assert every_number == {"AA10000070", "BB10000070"}
    assert body["totals"]["issued_total"] == "1050"

    # 另一家店看到的是自己的那些
    other_body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(other_mgr),
        )
    ).json()
    assert {row["number"] for row in other_body["issued"]} == {"ZZ99999999", "ZZ77777777"}
    assert [row["number"] for row in other_body["manual_paper_adjustments"]] == ["ZZ77777777"]


async def test_invoice_register_shows_earlier_invoices_voided_this_period(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """8/31 開、9/2 才完成作廢的票：9 月月報要看得到，否則沒人知道要去辦上期更正。

    用開立日歸期的話，它既不在 9 月的銷項、也不在 9 月的作廢，完全消失。
    """
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    issued_when = datetime(2026, 8, 31, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="1050", when=issued_when)
    invoice_id = await _invoice(
        db_session,
        store_id,
        sale_id,
        no="AA20260831V",
        when=date(2026, 8, 31),
        total="1050",
        status=InvoiceStatus.VOID,
        void_reason=InvoiceVoidReason.SALE_VOID,
    )
    db_session.add(
        EInvoiceUploadQueue(
            store_id=store_id,
            action=EInvoiceAction.VOID,
            message_type="F0501",
            invoice_id=invoice_id,
            status=UploadStatus.UPLOADED,
            uploaded_at=datetime(2026, 9, 2, 3, 0, tzinfo=UTC),  # 本期完成作廢
        )
    )
    await db_session.flush()

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    assert [row["number"] for row in body["voided_from_earlier_periods"]] == ["AA20260831V"]
    assert body["voided"] == []  # 本期開立的作廢才進這段
    assert body["totals"]["voided_total"] == "0"  # 上期的數字不混進本期合計


async def test_invoice_register_does_not_double_list_same_period_voids(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """本期開立、本期作廢的只出現在「作廢」，不重複列進前期那段。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    when = datetime(2026, 9, 5, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="500", when=when)
    invoice_id = await _invoice(
        db_session,
        store_id,
        sale_id,
        no="AA10000080",
        when=date(2026, 9, 5),
        total="500",
        status=InvoiceStatus.VOID,
        void_reason=InvoiceVoidReason.SALE_VOID,
    )
    db_session.add(
        EInvoiceUploadQueue(
            store_id=store_id,
            action=EInvoiceAction.VOID,
            message_type="F0501",
            invoice_id=invoice_id,
            status=UploadStatus.UPLOADED,
            uploaded_at=datetime(2026, 9, 6, 3, 0, tzinfo=UTC),
        )
    )
    await db_session.flush()

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    assert [row["number"] for row in body["voided"]] == ["AA10000080"]
    assert body["voided_from_earlier_periods"] == []
    assert body["totals"]["voided_total"] == "500"


async def test_invoice_register_rejects_an_inverted_period(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """迄早於起：拒絕而不是回一份空報表（空報表會被當成「本期沒有發票」）。"""
    mgr, _clerk, _store_id, _clerk_id = await _seed(db_session)
    resp = await client.get(
        "/api/v1/reports/invoice-register",
        params={"from": "2026-10-01T00:00:00+08:00", "to": "2026-09-01T00:00:00+08:00"},
        headers=_auth(mgr),
    )
    assert resp.status_code == 422


async def test_invoice_register_shows_paper_invoices_voided_this_period(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """手開紙本的跨期作廢也要看得到：它不排 F0501，只留稽核紀錄。

    8/31 開的紙本、9/2 店長確認紙本已作廢 → 9 月月報若什麼都不顯示，
    沒人知道要回頭更正 8 月已申報的銷項（與電子發票那條是同一種失敗）。
    """
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    issued_when = datetime(2026, 8, 31, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="1050", when=issued_when)
    invoice = Invoice(
        store_id=store_id,
        sale_id=sale_id,
        invoice_type=InvoiceType.B2C,
        invoice_no="MP20260831V",
        invoice_date=date(2026, 8, 31),
        status=InvoiceStatus.VOID,
        void_reason=InvoiceVoidReason.SALE_VOID,
        issue_channel=EInvoiceIssueChannel.MANUAL_PAPER,
        net=Decimal(1000),
        tax=Decimal(50),
        total=Decimal(1050),
        created_at=issued_when,
    )
    db_session.add(invoice)
    await db_session.flush()
    db_session.add(
        AuditLog(
            store_id=store_id,
            actor_user_id=clerk_id,
            action="VOID_INVOICE",
            entity_type="invoice",
            entity_id=str(invoice.id),
            after={"source": "manual_paper_disposed"},
            created_at=datetime(2026, 9, 2, 3, 0, tzinfo=UTC),
        )
    )
    await db_session.flush()

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    assert [row["number"] for row in body["voided_from_earlier_periods"]] == ["MP20260831V"]


async def test_invoice_register_counts_an_electronic_void_in_one_period_only(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """電子票 8/31 申請作廢、9/1 平台核可：只能算在 9 月，不可兩個月各列一次。

    每次作廢狀態轉移都會寫稽核，若稽核那一臂也認電子票，8 月（申請）與 9 月（核可）
    會各出現一次，會計以為要辦兩次更正。
    """
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    issued_when = datetime(2026, 8, 20, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="1050", when=issued_when)
    invoice_id = await _invoice(
        db_session,
        store_id,
        sale_id,
        no="AA20260820V",
        when=date(2026, 8, 20),
        total="1050",
        status=InvoiceStatus.VOID,
        void_reason=InvoiceVoidReason.SALE_VOID,
    )
    db_session.add(
        AuditLog(  # 8/31 店員按下作廢（轉 VOID_PENDING）
            store_id=store_id,
            actor_user_id=clerk_id,
            action="VOID_INVOICE",
            entity_type="invoice",
            entity_id=str(invoice_id),
            after={"source": "STAFF"},
            created_at=datetime(2026, 8, 31, 15, 50, tzinfo=UTC),
        )
    )
    db_session.add(
        EInvoiceUploadQueue(  # 9/1 平台核可
            store_id=store_id,
            action=EInvoiceAction.VOID,
            message_type="F0501",
            invoice_id=invoice_id,
            status=UploadStatus.UPLOADED,
            uploaded_at=datetime(2026, 9, 1, 2, 0, tzinfo=UTC),
        )
    )
    await db_session.flush()

    async def voided_earlier(date_from: str, date_to: str) -> list[str]:
        body = (
            await client.get(
                "/api/v1/reports/invoice-register",
                params={"from": date_from, "to": date_to},
                headers=_auth(mgr),
            )
        ).json()
        return [str(row["number"]) for row in body["voided_from_earlier_periods"]]

    august = await voided_earlier("2026-08-01T00:00:00+08:00", "2026-09-01T00:00:00+08:00")
    september = await voided_earlier("2026-09-01T00:00:00+08:00", "2026-10-01T00:00:00+08:00")
    assert august == []  # 申請作廢那個月不算
    assert september == ["AA20260820V"]  # 平台核可那個月才算，且只算一次


async def test_invoice_register_excludes_unnumbered_drafts_voided_in_a_later_period(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """從未配號的草稿跨月作廢：平台上沒有那張票，不能列成作廢稅單（與本期同一口徑）。"""
    mgr, _clerk, store_id, clerk_id = await _seed(db_session)
    sale_when = datetime(2026, 8, 20, 6, 0, tzinfo=UTC)
    sale_id = await _sale(db_session, store_id, clerk_id, total="700", when=sale_when)
    draft = Invoice(
        store_id=store_id,
        sale_id=sale_id,
        invoice_type=InvoiceType.B2C,
        invoice_no=None,  # 從未開立成功
        invoice_date=None,
        status=InvoiceStatus.VOID,
        void_reason=InvoiceVoidReason.SALE_VOID,
        net=Decimal(667),
        tax=Decimal(33),
        total=Decimal(700),
        created_at=sale_when,
    )
    db_session.add(draft)
    await db_session.flush()
    db_session.add(
        AuditLog(
            store_id=store_id,
            actor_user_id=clerk_id,
            action="VOID_INVOICE",
            entity_type="invoice",
            entity_id=str(draft.id),
            after={"source": "STAFF"},
            created_at=datetime(2026, 9, 3, 3, 0, tzinfo=UTC),
        )
    )
    await db_session.flush()

    body = (
        await client.get(
            "/api/v1/reports/invoice-register",
            params={"from": "2026-09-01T00:00:00+08:00", "to": "2026-10-01T00:00:00+08:00"},
            headers=_auth(mgr),
        )
    ).json()
    assert body["voided_from_earlier_periods"] == []
    assert body["voided"] == []
