"""混合付款的發票不含購物金（設定「扣掉購物金後開發票」，預設；店主 2026-10-08）。

情境：帳篷 5 件 × $200 = $1,000，購物金 $300＋現金 $700。
- 發票只開 $700：品項金額依比例扣掉購物金，不列負數行、不開折讓。
- 退貨時購物金優先退回；購物金那部分本來就不在發票上，所以折讓只算非購物金的退款，
  只退了購物金的那次不用折讓、也不用客人簽名。
- 同月整筆退照舊作廢發票（ADR-014）。
- 餐點也能用購物金（店主 2026-10-09）：純餐點的混合付款同樣扣掉購物金後開，
  退貨購物金優先、折讓只算現金那部分，累計折讓剛好等於發票金額。
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from itertools import count
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.contacts.models import Contact
from app.modules.einvoice.models import EInvoiceUploadQueue, Invoice, InvoiceAllowance
from app.modules.einvoice.service import EInvoiceService
from app.modules.inventory.models import CatalogProduct
from app.modules.menu.service import MenuService
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.service import SalesService
from app.modules.storecredit.service import StoreCreditService
from app.shared.enums import (
    EInvoiceAction,
    InvoiceStatus,
    SaleInvoiceStatus,
    SaleLineType,
    ServiceMode,
    StoreCreditInvoiceMode,
    TenderType,
    UploadStatus,
)
from tests.integration.customer_display_helpers import (
    prepare_signed_store_credit_cart,
    signed_return_consent,
)
from tests.integration.test_einvoice_amego_send import (
    _client,
    _issue_ok_transport,
    _issue_queue_id,
    _seed,
)

_seq = count(1)


async def _mixed_sale(
    session: AsyncSession, *, credit: str = "300", cash: str = "700"
) -> tuple[int, int, int]:
    """開一筆購物金＋現金的 $1,000 交易；回傳 (store_id, clerk_id, sale_id)。"""
    store_id, clerk_id, _code = await _seed(session)
    n = next(_seq)
    member = Contact(store_id=store_id, name=f"混合會員{n}", roles=["MEMBER"])
    product = CatalogProduct(
        store_id=store_id,
        sku=f"SC-NET-{n}",
        name="帳篷",
        unit_price=Decimal("200"),
        quantity_on_hand=10,
    )
    session.add_all([member, product])
    await session.flush()
    await StoreCreditService(session).adjust(
        store_id,
        member.id,
        amount=Decimal(credit),
        reason="購物金發票測試",
        created_by=clerk_id,
        idempotency_key=f"sc-net-credit-{n}",
    )
    tenders_payload: list[dict[str, str]] = [{"tender_type": "STORE_CREDIT", "amount": credit}]
    tenders = [TenderInput(tender_type=TenderType.STORE_CREDIT, amount=Decimal(credit))]
    if Decimal(cash) > 0:
        tenders_payload.append({"tender_type": "CASH", "amount": cash})
        tenders.append(TenderInput(tender_type=TenderType.CASH, amount=Decimal(cash)))
    signed = await prepare_signed_store_credit_cart(
        session,
        store_id=store_id,
        actor_user_id=clerk_id,
        payload={
            "buyer_contact_id": member.id,
            "lines": [{"line_type": "CATALOG", "catalog_product_id": product.id, "qty": 5}],
            "tenders": tenders_payload,
        },
    )
    sale = await SalesService(session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.CATALOG, catalog_product_id=product.id, qty=5)],
        buyer_contact_id=member.id,
        tenders=tenders,
        idempotency_key=f"sc-net-sale-{n}",
        signature_task_id=signed.signature_task_id,
        cart_session_id=signed.cart_session_id,
        cart_revision=signed.cart_revision,
    )
    return store_id, clerk_id, sale.id


async def _issue(session: AsyncSession, store_id: int) -> list[tuple[str, dict[str, str]]]:
    svc = EInvoiceService(session)
    transport = _issue_ok_transport()
    await svc.send_via_amego(
        store_id, await _issue_queue_id(svc, store_id), client=_client(transport)
    )
    return transport.calls


async def _invoice(session: AsyncSession, sale_id: int) -> Invoice:
    invoice = await session.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
    assert invoice is not None
    await session.refresh(invoice)
    return invoice


async def _allowance_totals(session: AsyncSession, sale_id: int) -> list[Decimal]:
    invoice = await _invoice(session, sale_id)
    rows = await session.scalars(
        select(InvoiceAllowance)
        .where(InvoiceAllowance.invoice_id == invoice.id)
        .order_by(InvoiceAllowance.id)
    )
    return [row.total for row in rows.all()]


async def _sale_status(session: AsyncSession, store_id: int, sale_id: int) -> SaleInvoiceStatus:
    sale = await SalesService(session).get_sale(store_id, sale_id)
    assert sale is not None
    await session.refresh(sale)
    return sale.invoice_status


async def _first_line_id(session: AsyncSession, sale_id: int) -> int:
    return (await SalesService(session).get_lines(sale_id))[0].id


async def _return(
    session: AsyncSession,
    store_id: int,
    clerk_id: int,
    sale_id: int,
    qty: int,
    key: str,
    *,
    consent: bool = True,
) -> None:
    line_id = await _first_line_id(session, sale_id)
    consent_id = None
    if consent:
        buyer = Contact(
            store_id=store_id, name="退貨客", roles=["MEMBER"], phone=f"09{next(_seq):08d}"
        )
        session.add(buyer)
        await session.flush()
        consent_id = await signed_return_consent(
            session,
            store_id=store_id,
            sale_id=sale_id,
            contact_id=buyer.id,
            created_by=clerk_id,
            return_lines={line_id: qty},
        )
    await ReturnsService(session).create_return(
        store_id,
        sale_id=sale_id,
        lines=[ReturnLineInput(sale_line_id=line_id, qty=qty)],
        reason="測試退貨",
        actor_user_id=clerk_id,
        idempotency_key=key,
        invoice_recalled=True,
        consent_signature_task_id=consent_id,
    )


# ── 結帳與開立 ─────────────────────────────────────────────────────────


async def test_mixed_sale_invoice_excludes_store_credit(db_session: AsyncSession) -> None:
    store_id, _clerk, sale_id = await _mixed_sale(db_session)
    sale = await SalesService(db_session).get_sale(store_id, sale_id)
    assert sale is not None and sale.total == Decimal(1000)  # 成交總額不變

    invoice = await _invoice(db_session, sale_id)
    assert (invoice.total, invoice.net, invoice.tax) == (Decimal(700), Decimal(667), Decimal(33))


async def test_f0401_items_are_net_of_store_credit(db_session: AsyncSession) -> None:
    """送出的 F0401：品項 5 × $140＝$700，不列「購物金折抵」負數行。"""
    store_id, _clerk, sale_id = await _mixed_sale(db_session)
    calls = await _issue(db_session, store_id)

    f0401 = json.loads(calls[1][1]["data"])
    items = f0401["ProductItem"]
    assert [(i["Description"], i["Quantity"], i["UnitPrice"], i["Amount"]) for i in items] == [
        ("帳篷", 5, "140", "700")
    ]
    assert f0401["SalesAmount"] == 700
    assert f0401["TotalAmount"] == 700
    assert (await _invoice(db_session, sale_id)).status is InvoiceStatus.ISSUED
    assert await _allowance_totals(db_session, sale_id) == []  # 不開折讓


# ── 退貨 ─────────────────────────────────────────────────────────────


async def test_return_refunded_only_as_store_credit_needs_no_allowance_or_signature(
    db_session: AsyncSession,
) -> None:
    """退 1 件 $200：購物金優先退回、全退購物金 → 發票本來就不含這 $200，不折讓、不用簽名。"""
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)
    line_id = await _first_line_id(db_session, sale_id)

    preview = await ReturnsService(db_session).preview_return(
        store_id, sale_id=sale_id, lines=[ReturnLineInput(sale_line_id=line_id, qty=1)]
    )
    assert preview["invoice_action"] == "NONE"
    assert preview["requires_customer_consent"] is False

    await _return(db_session, store_id, clerk_id, sale_id, 1, "net-ret-sc", consent=False)
    assert await _allowance_totals(db_session, sale_id) == []
    assert await _sale_status(db_session, store_id, sale_id) is SaleInvoiceStatus.ISSUED


async def test_return_allowance_only_covers_the_non_store_credit_refund(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)

    await _return(db_session, store_id, clerk_id, sale_id, 1, "net-ret-1", consent=False)
    # 再退 1 件：購物金只剩 $100 可退 → 現金 $100 → 折讓 $100
    await _return(db_session, store_id, clerk_id, sale_id, 1, "net-ret-2")
    assert await _allowance_totals(db_session, sale_id) == [Decimal(100)]
    # 剩下 3 件：現金 $600 → 折讓 $600；累計剛好等於發票金額 $700
    await _return(db_session, store_id, clerk_id, sale_id, 3, "net-ret-3")
    assert await _allowance_totals(db_session, sale_id) == [Decimal(100), Decimal(600)]
    assert sum(await _allowance_totals(db_session, sale_id), Decimal(0)) == Decimal(700)


async def test_return_before_issue_is_backfilled_with_the_non_store_credit_part(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    # 發票還沒開立就退了 2 件（$400：購物金 $300＋現金 $100）
    await _return(db_session, store_id, clerk_id, sale_id, 2, "net-pre-1", consent=False)
    assert (await _invoice(db_session, sale_id)).status is InvoiceStatus.PENDING

    await _issue(db_session, store_id)
    assert await _allowance_totals(db_session, sale_id) == [Decimal(100)]


async def test_same_month_full_return_still_voids_the_invoice(db_session: AsyncSession) -> None:
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)
    invoice = await _invoice(db_session, sale_id)
    # 回放的 F0401 回應帶固定日期；改成今天（台北）才是「同月」
    invoice.invoice_date = datetime.now(UTC).astimezone(ZoneInfo("Asia/Taipei")).date()
    await db_session.flush()

    await _return(db_session, store_id, clerk_id, sale_id, 5, "net-full")

    assert await _allowance_totals(db_session, sale_id) == []
    voids = [
        item
        for item in (
            await db_session.scalars(
                select(EInvoiceUploadQueue).where(EInvoiceUploadQueue.invoice_id == invoice.id)
            )
        ).all()
        if item.action is EInvoiceAction.VOID
    ]
    assert [v.status for v in voids] == [UploadStatus.PENDING]
    assert await _sale_status(db_session, store_id, sale_id) is SaleInvoiceStatus.PENDING_VOID


# ── 結帳記下的開票方式 ──────────────────────────────────────────────


async def test_invoice_records_the_mode_used_at_checkout(db_session: AsyncSession) -> None:
    _store_id, _clerk, sale_id = await _mixed_sale(db_session)
    invoice = await _invoice(db_session, sale_id)
    assert invoice.store_credit_mode is StoreCreditInvoiceMode.DEDUCT


async def test_cash_only_sale_has_no_store_credit_mode(db_session: AsyncSession) -> None:
    store_id, clerk_id, code = await _seed(db_session)
    sale = await SalesService(db_session).create_sale(
        store_id, clerk_id, lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)]
    )
    invoice = await _invoice(db_session, sale.id)
    assert invoice.total == sale.total
    assert invoice.store_credit_mode is None


# ── 純餐點用購物金（店主 2026-10-09）────────────────────────────────────


async def _food_mixed_sale(session: AsyncSession) -> tuple[int, int, int]:
    """拿鐵 2 杯 × $150＝$300，購物金 $100＋現金 $200；回傳 (store_id, clerk_id, sale_id)。"""
    store_id, clerk_id, _code = await _seed(session)
    n = next(_seq)
    member = Contact(store_id=store_id, name=f"餐點會員{n}", roles=["MEMBER"])
    session.add(member)
    await session.flush()
    latte = await MenuService(session).create_menu_item(
        store_id, name=f"拿鐵{n}", unit_price=Decimal(150), actor_user_id=clerk_id
    )
    await StoreCreditService(session).adjust(
        store_id,
        member.id,
        amount=Decimal(100),
        reason="餐點購物金發票測試",
        created_by=clerk_id,
        idempotency_key=f"sc-food-credit-{n}",
    )
    signed = await prepare_signed_store_credit_cart(
        session,
        store_id=store_id,
        actor_user_id=clerk_id,
        payload={
            "buyer_contact_id": member.id,
            "lines": [{"line_type": "MENU", "menu_item_id": latte.id, "qty": 2}],
            "tenders": [
                {"tender_type": "STORE_CREDIT", "amount": "100"},
                {"tender_type": "CASH", "amount": "200"},
            ],
            "service_mode": "TAKEOUT",
        },
    )
    sale = await SalesService(session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.MENU, menu_item_id=latte.id, qty=2)],
        buyer_contact_id=member.id,
        tenders=[
            TenderInput(tender_type=TenderType.STORE_CREDIT, amount=Decimal(100)),
            TenderInput(tender_type=TenderType.CASH, amount=Decimal(200)),
        ],
        idempotency_key=f"sc-food-sale-{n}",
        service_mode=ServiceMode.TAKEOUT,
        signature_task_id=signed.signature_task_id,
        cart_session_id=signed.cart_session_id,
        cart_revision=signed.cart_revision,
    )
    return store_id, clerk_id, sale.id


async def test_food_only_store_credit_sale_invoices_net_and_allowances_add_up(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id, sale_id = await _food_mixed_sale(db_session)
    invoice = await _invoice(db_session, sale_id)
    assert invoice.total == Decimal(200)

    calls = await _issue(db_session, store_id)
    f0401 = json.loads(calls[1][1]["data"])
    assert [(i["Quantity"], i["UnitPrice"], i["Amount"]) for i in f0401["ProductItem"]] == [
        (2, "100", "200")
    ]
    assert f0401["TotalAmount"] == 200

    # 退 1 杯 $150：購物金 $100 先退、現金 $50 → 折讓 $50
    await _return(db_session, store_id, clerk_id, sale_id, 1, "food-ret-1")
    assert await _allowance_totals(db_session, sale_id) == [Decimal(50)]
    # 再退 1 杯：全是現金 $150 → 折讓 $150；累計剛好等於發票金額 $200
    await _return(db_session, store_id, clerk_id, sale_id, 1, "food-ret-2")
    assert await _allowance_totals(db_session, sale_id) == [Decimal(50), Decimal(150)]
