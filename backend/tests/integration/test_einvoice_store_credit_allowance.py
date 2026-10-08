"""混合付款的購物金部分以折讓處理（設定「整筆開＋購物金折讓」；ADR-029，店主 2026-10-07）。

情境：帳篷 5 件 × $200 = $1,000，購物金 $300＋現金 $700。
- 結帳照整筆 $1,000 開發票；平台確認開立後自動開一張 $300 的「購物金折讓」（G0401），
  一張發票至多一張；交易的發票狀態維持「已開立」。
- 整筆作廢：先作廢購物金折讓（G0501），平台受理後才作廢發票（F0501）。折讓還沒送出就直接
  取消；送出中就等結果；平台退回（從未成立）就視為已作廢。
- 退貨：已有購物金折讓時，折讓金額＝本次退款中非購物金的部分。
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from itertools import count
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.contacts.models import Contact
from app.modules.einvoice.background_service import (
    AUTO_SEND_ACTIONS,
    AUTO_SEND_MESSAGE_TYPES,
    AUTO_SEND_RETRY_INTERVAL,
)
from app.modules.einvoice.models import EInvoiceUploadQueue, Invoice, InvoiceAllowance
from app.modules.einvoice.service import EInvoiceService
from app.modules.inventory.models import CatalogProduct
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import SaleLineInput, TenderInput
from app.modules.sales.service import SalesService
from app.modules.storecredit.service import StoreCreditService
from app.shared.enums import (
    EInvoiceAction,
    EInvoiceMessageType,
    InvoiceAllowanceSource,
    InvoiceStatus,
    InvoiceVoidReason,
    SaleInvoiceStatus,
    SaleLineType,
    TenderType,
    UploadStatus,
)
from app.shared.exceptions import AmegoTransportError, EInvoiceQueueNotRetryable
from tests.integration.customer_display_helpers import (
    prepare_signed_store_credit_cart,
    signed_return_consent,
)
from tests.integration.legacy_store_credit_helpers import mark_legacy_allowance_invoice
from tests.integration.test_einvoice_amego_send import (
    _QUERY_ALLOWANCE_NOT_FOUND,
    _client,
    _issue_ok_transport,
    _issue_queue_id,
    _now_epoch,
    _ScriptedTransport,
    _seed,
)

_seq = count(1)

# 本檔的混合付款單總額 1000（購物金 300＋現金 700）；作廢前對帳查到的發票金額要相符。
_QUERY_INVOICE_OPEN_1000 = {
    "code": 0,
    "msg": "",
    "data": {"invoice_type": "C0401", "total_amount": 1000, "invoice_status": 99},
}


def _allowance_exists(net: int, tax: int) -> dict[str, object]:
    return {
        "code": 0,
        "msg": "",
        "data": {
            "invoice_type": "D0401",
            "invoice_status": 99,
            "total_amount": net,
            "tax_amount": tax,
            "product_item": [{"original_invoice_number": "AB00001111"}],
            "create_date": _now_epoch(),  # 剛建立＝確為本筆（G0401 對帳的時間鑑別）
        },
    }


def _allowance_voided(net: int, tax: int) -> dict[str, object]:
    resp = _allowance_exists(net, tax)
    data = resp["data"]
    assert isinstance(data, dict)
    data["invoice_type"] = "D0501"
    return resp


async def _mixed_sale(
    session: AsyncSession, *, credit: str = "300", cash: str = "700"
) -> tuple[int, int, int]:
    """開一筆購物金＋現金的 $1,000 交易；回傳 (store_id, clerk_id, sale_id)。"""
    store_id, clerk_id, _code = await _seed(session)
    n = next(_seq)
    member = Contact(store_id=store_id, name=f"混合會員{n}", roles=["MEMBER"])
    product = CatalogProduct(
        store_id=store_id,
        sku=f"SC-ALLOW-{n}",
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
        reason="購物金折讓測試",
        created_by=clerk_id,
        idempotency_key=f"sc-allow-credit-{n}",
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
        idempotency_key=f"sc-allow-sale-{n}",
        signature_task_id=signed.signature_task_id,
        cart_session_id=signed.cart_session_id,
        cart_revision=signed.cart_revision,
    )
    # 新單一律扣掉購物金後開；這裡模擬舊模式（整筆開＋購物金折讓）時期開出的發票。
    await mark_legacy_allowance_invoice(session, sale.id)
    return store_id, clerk_id, sale.id


async def _issue(session: AsyncSession, store_id: int) -> None:
    svc = EInvoiceService(session)
    await svc.send_via_amego(
        store_id, await _issue_queue_id(svc, store_id), client=_client(_issue_ok_transport())
    )


async def _allowances(session: AsyncSession, sale_id: int) -> list[InvoiceAllowance]:
    invoice = await session.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
    assert invoice is not None
    rows = await session.scalars(
        select(InvoiceAllowance)
        .where(InvoiceAllowance.invoice_id == invoice.id)
        .order_by(InvoiceAllowance.id)
    )
    found = list(rows.all())
    for row in found:
        await session.refresh(row)
    return found


async def _queue(session: AsyncSession, store_id: int) -> list[EInvoiceUploadQueue]:
    rows = list(
        (await session.scalars(select(EInvoiceUploadQueue).order_by(EInvoiceUploadQueue.id))).all()
    )
    for row in rows:
        await session.refresh(row)
    return [r for r in rows if r.store_id == store_id]


def _pending(items: list[EInvoiceUploadQueue], action: EInvoiceAction) -> list[EInvoiceUploadQueue]:
    return [i for i in items if i.action is action and i.status is UploadStatus.PENDING]


async def _send(
    session: AsyncSession, store_id: int, item: EInvoiceUploadQueue, *responses: object
) -> _ScriptedTransport:
    transport = _ScriptedTransport(*responses)
    await EInvoiceService(session).send_via_amego(store_id, item.id, client=_client(transport))
    return transport


async def _invoice(session: AsyncSession, sale_id: int) -> Invoice:
    invoice = await session.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
    assert invoice is not None
    await session.refresh(invoice)
    return invoice


async def _sale_status(session: AsyncSession, store_id: int, sale_id: int) -> SaleInvoiceStatus:
    sale = await SalesService(session).get_sale(store_id, sale_id)
    assert sale is not None
    await session.refresh(sale)
    return sale.invoice_status


# ── 開立 ─────────────────────────────────────────────────────────────


async def test_mixed_sale_issues_full_invoice_then_store_credit_allowance(
    db_session: AsyncSession,
) -> None:
    store_id, _clerk, sale_id = await _mixed_sale(db_session)
    invoice = await _invoice(db_session, sale_id)
    assert invoice.total == Decimal(1000)  # 發票照整筆
    assert await _allowances(db_session, sale_id) == []  # 平台確認開立前不開折讓

    await _issue(db_session, store_id)

    [allowance] = await _allowances(db_session, sale_id)
    assert allowance.source is InvoiceAllowanceSource.STORE_CREDIT
    assert (allowance.total, allowance.net, allowance.tax) == (
        Decimal(300),
        Decimal(286),
        Decimal(14),
    )
    assert allowance.return_id is None
    # 這是購物金的稅務處理，不是退貨：交易仍顯示已開立
    assert await _sale_status(db_session, store_id, sale_id) is SaleInvoiceStatus.ISSUED

    [g0401] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE)
    transport = await _send(
        db_session, store_id, g0401, dict(_QUERY_ALLOWANCE_NOT_FOUND), {"code": 0, "msg": ""}
    )
    entry = json.loads(transport.calls[1][1]["data"])[0]
    assert entry["ProductItem"][0]["OriginalDescription"] == "購物金折抵折讓"
    assert (entry["TotalAmount"], entry["TaxAmount"]) == (286, 14)
    await db_session.refresh(allowance)
    assert allowance.allowance_no == allowance.platform_number
    # G0401 核可後交易一樣不變成「已折讓」
    assert await _sale_status(db_session, store_id, sale_id) is SaleInvoiceStatus.ISSUED


async def test_store_credit_allowance_is_created_only_once(db_session: AsyncSession) -> None:
    store_id, _clerk, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)
    invoice = await _invoice(db_session, sale_id)
    svc = EInvoiceService(db_session)
    again = await svc.ensure_store_credit_allowance(store_id, invoice.id)
    [allowance] = await _allowances(db_session, sale_id)
    assert again is not None and again.id == allowance.id


async def test_cash_only_sale_gets_no_store_credit_allowance(db_session: AsyncSession) -> None:
    store_id, clerk_id, code = await _seed(db_session)
    sale = await SalesService(db_session).create_sale(
        store_id, clerk_id, lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)]
    )
    await _issue(db_session, store_id)
    assert await _allowances(db_session, sale.id) == []


async def test_autosend_picks_up_allowance_void(db_session: AsyncSession) -> None:
    """背景自動送出真的會撈到 G0501（動作與訊息型別兩道白名單都要放行）。"""
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)
    [g0401] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE)
    await _send(
        db_session, store_id, g0401, dict(_QUERY_ALLOWANCE_NOT_FOUND), {"code": 0, "msg": ""}
    )
    await _void_sale(db_session, store_id, clerk_id, sale_id)
    [g0501] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE_VOID)

    due = await EInvoiceService(db_session).list_due_auto_send_items(
        actions=AUTO_SEND_ACTIONS,
        message_types=AUTO_SEND_MESSAGE_TYPES,
        idle_since=datetime.now(UTC) - AUTO_SEND_RETRY_INTERVAL,
        limit=50,
    )
    assert g0501.id in [item.id for item in due]


# ── 整筆作廢 ─────────────────────────────────────────────────────────


async def _void_sale(session: AsyncSession, store_id: int, clerk_id: int, sale_id: int) -> None:
    sales = SalesService(session)
    sale = await sales.get_sale(store_id, sale_id)
    assert sale is not None
    await sales.void_sale(sale, clerk_id)


async def test_void_after_allowance_accepted_voids_allowance_first_then_invoice(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)
    [g0401] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE)
    await _send(
        db_session, store_id, g0401, dict(_QUERY_ALLOWANCE_NOT_FOUND), {"code": 0, "msg": ""}
    )

    await _void_sale(db_session, store_id, clerk_id, sale_id)

    invoice = await _invoice(db_session, sale_id)
    assert invoice.status is InvoiceStatus.VOID_PENDING
    queue = await _queue(db_session, store_id)
    assert _pending(queue, EInvoiceAction.VOID) == []  # 發票作廢要等折讓作廢完成
    [g0501] = _pending(queue, EInvoiceAction.ALLOWANCE_VOID)
    assert g0501.message_type is EInvoiceMessageType.G0501

    [allowance] = await _allowances(db_session, sale_id)
    transport = await _send(
        db_session, store_id, g0501, _allowance_exists(286, 14), {"code": 0, "msg": ""}
    )
    assert transport.calls[0][0].endswith("/json/allowance_query")  # 對帳先行
    assert transport.calls[1][0].endswith("/json/g0501")
    assert json.loads(transport.calls[1][1]["data"]) == [
        {"CancelAllowanceNumber": allowance.platform_number}
    ]
    await db_session.refresh(allowance)
    assert allowance.voided is True

    [f0501] = _pending(await _queue(db_session, store_id), EInvoiceAction.VOID)
    transport = await _send(
        db_session,
        store_id,
        f0501,
        dict(_QUERY_INVOICE_OPEN_1000),
        _allowance_voided(286, 14),  # 平台已處理完折讓作廢
        {"code": 0, "msg": ""},
    )
    assert transport.calls[2][0].endswith("/json/f0501")
    invoice = await _invoice(db_session, sale_id)
    assert invoice.status is InvoiceStatus.VOID
    assert await _sale_status(db_session, store_id, sale_id) is SaleInvoiceStatus.VOID


async def test_void_before_allowance_sent_cancels_it_and_voids_invoice(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)  # G0401 還在佇列、沒送

    await _void_sale(db_session, store_id, clerk_id, sale_id)

    queue = await _queue(db_session, store_id)
    [g0401] = [i for i in queue if i.action is EInvoiceAction.ALLOWANCE]
    assert g0401.status is UploadStatus.CANCELLED
    assert _pending(queue, EInvoiceAction.ALLOWANCE_VOID) == []  # 平台從沒收過，不用作廢折讓
    assert len(_pending(queue, EInvoiceAction.VOID)) == 1
    [allowance] = await _allowances(db_session, sale_id)
    assert allowance.voided is True


async def test_void_while_allowance_in_flight_waits_for_its_result(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)
    [g0401] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE)
    # 送出後回應遺失：已認領、結果未知
    try:
        await _send(
            db_session,
            store_id,
            g0401,
            dict(_QUERY_ALLOWANCE_NOT_FOUND),
            AmegoTransportError("斷線"),
        )
    except AmegoTransportError:
        pass

    await _void_sale(db_session, store_id, clerk_id, sale_id)
    queue = await _queue(db_session, store_id)
    assert _pending(queue, EInvoiceAction.ALLOWANCE_VOID) == []
    assert _pending(queue, EInvoiceAction.VOID) == []
    assert (await _invoice(db_session, sale_id)).status is InvoiceStatus.VOID_PENDING

    # 對帳發現平台其實已開立 → 續送 G0501
    [g0401] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE)
    await _send(db_session, store_id, g0401, _allowance_exists(286, 14))
    queue = await _queue(db_session, store_id)
    assert len(_pending(queue, EInvoiceAction.ALLOWANCE_VOID)) == 1
    assert _pending(queue, EInvoiceAction.VOID) == []


async def test_void_after_allowance_rejected_treats_it_as_never_existed(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)
    [g0401] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE)
    await _send(
        db_session, store_id, g0401, dict(_QUERY_ALLOWANCE_NOT_FOUND), {"code": 1, "msg": "拒絕"}
    )

    await _void_sale(db_session, store_id, clerk_id, sale_id)

    queue = await _queue(db_session, store_id)
    [g0401] = [i for i in queue if i.action is EInvoiceAction.ALLOWANCE]
    assert g0401.status is UploadStatus.CANCELLED  # 不會再被重送
    assert _pending(queue, EInvoiceAction.ALLOWANCE_VOID) == []
    assert len(_pending(queue, EInvoiceAction.VOID)) == 1
    [allowance] = await _allowances(db_session, sale_id)
    assert allowance.voided is True


async def test_allowance_void_reconcile_skips_resend_when_platform_already_voided(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)
    [g0401] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE)
    await _send(
        db_session, store_id, g0401, dict(_QUERY_ALLOWANCE_NOT_FOUND), {"code": 0, "msg": ""}
    )
    await _void_sale(db_session, store_id, clerk_id, sale_id)
    [g0501] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE_VOID)

    transport = await _send(db_session, store_id, g0501, _allowance_voided(286, 14))

    assert len(transport.calls) == 1  # 只查、不重送
    [allowance] = await _allowances(db_session, sale_id)
    assert allowance.voided is True
    assert len(_pending(await _queue(db_session, store_id), EInvoiceAction.VOID)) == 1


# ── 退貨 ─────────────────────────────────────────────────────────────


async def _return(
    session: AsyncSession, store_id: int, clerk_id: int, sale_id: int, qty: int, key: str
) -> None:
    line = (await SalesService(session).get_lines(sale_id))[0]
    buyer = Contact(store_id=store_id, name="退貨客", roles=["MEMBER"], phone=f"09{next(_seq):08d}")
    session.add(buyer)
    await session.flush()
    consent = await signed_return_consent(
        session,
        store_id=store_id,
        sale_id=sale_id,
        contact_id=buyer.id,
        created_by=clerk_id,
        return_lines={line.id: qty},
    )
    await ReturnsService(session).create_return(
        store_id,
        sale_id=sale_id,
        lines=[ReturnLineInput(sale_line_id=line.id, qty=qty)],
        reason="測試退貨",
        actor_user_id=clerk_id,
        idempotency_key=key,
        invoice_recalled=True,
        consent_signature_task_id=consent,
    )


async def _return_allowance_totals(session: AsyncSession, sale_id: int) -> list[Decimal]:
    return [
        a.total
        for a in await _allowances(session, sale_id)
        if a.source is InvoiceAllowanceSource.RETURN
    ]


async def test_return_allowance_excludes_store_credit_refund(db_session: AsyncSession) -> None:
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)

    # 退 1 件 $200：購物金優先退回 → 沒有非購物金退款 → 不開退貨折讓
    await _return(db_session, store_id, clerk_id, sale_id, 1, "sc-ret-1")
    assert await _return_allowance_totals(db_session, sale_id) == []
    # 再退 1 件：購物金只剩 $100 可退 → 現金 $100 → 折讓 $100
    await _return(db_session, store_id, clerk_id, sale_id, 1, "sc-ret-2")
    assert await _return_allowance_totals(db_session, sale_id) == [Decimal(100)]
    # 剩下 3 件全退：現金 $600 → 折讓 $600；累計 300＋100＋600＝發票總額
    await _return(db_session, store_id, clerk_id, sale_id, 3, "sc-ret-3")
    assert await _return_allowance_totals(db_session, sale_id) == [Decimal(100), Decimal(600)]
    total = sum((a.total for a in await _allowances(db_session, sale_id)), Decimal(0))
    assert total == (await _invoice(db_session, sale_id)).total


async def test_return_before_issue_is_backfilled_after_store_credit_allowance(
    db_session: AsyncSession,
) -> None:
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    # 發票還沒開立就退了 2 件（$400：購物金 $300＋現金 $100）
    await _return(db_session, store_id, clerk_id, sale_id, 2, "sc-pre-1")
    assert (await _invoice(db_session, sale_id)).status is InvoiceStatus.PENDING

    await _issue(db_session, store_id)

    sources = [(a.source, a.total) for a in await _allowances(db_session, sale_id)]
    assert sources == [
        (InvoiceAllowanceSource.STORE_CREDIT, Decimal(300)),
        (InvoiceAllowanceSource.RETURN, Decimal(100)),
    ]


async def test_same_month_full_return_voids_store_credit_allowance_then_invoice(
    db_session: AsyncSession,
) -> None:
    """同月整筆退照 ADR-014 作廢發票；購物金折讓不算「已折讓過」，作廢前先 G0501 作廢它。"""
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)
    [g0401] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE)
    await _send(
        db_session, store_id, g0401, dict(_QUERY_ALLOWANCE_NOT_FOUND), {"code": 0, "msg": ""}
    )
    # 回放的 F0401 回應帶固定日期；改成今天（台北）才是「同月」
    invoice = await _invoice(db_session, sale_id)
    invoice.invoice_date = datetime.now(UTC).astimezone(ZoneInfo("Asia/Taipei")).date()
    await db_session.flush()

    await _return(db_session, store_id, clerk_id, sale_id, 5, "sc-full")

    assert await _return_allowance_totals(db_session, sale_id) == []
    queue = await _queue(db_session, store_id)
    assert len(_pending(queue, EInvoiceAction.ALLOWANCE_VOID)) == 1
    assert _pending(queue, EInvoiceAction.VOID) == []  # 等折讓作廢完才送 F0501
    assert (await _invoice(db_session, sale_id)).status is InvoiceStatus.VOID_PENDING
    assert await _sale_status(db_session, store_id, sale_id) is SaleInvoiceStatus.PENDING_VOID


async def test_invoice_void_waits_until_platform_finishes_allowance_void(
    db_session: AsyncSession,
) -> None:
    """光貿測試環境實測（2026-10-08）：G0501 受理後折讓作廢先進 `wait[]`，此時送 F0501 會被拒
    （3050141 已存在折讓單）。平台處理完（invoice_type 轉 D0501）之前不送 F0501、維持待送出。"""
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)
    [g0401] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE)
    await _send(
        db_session, store_id, g0401, dict(_QUERY_ALLOWANCE_NOT_FOUND), {"code": 0, "msg": ""}
    )
    await _void_sale(db_session, store_id, clerk_id, sale_id)
    [g0501] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE_VOID)
    await _send(db_session, store_id, g0501, _allowance_exists(286, 14), {"code": 0, "msg": ""})
    [f0501] = _pending(await _queue(db_session, store_id), EInvoiceAction.VOID)

    queued = _allowance_exists(286, 14)
    data = queued["data"]
    assert isinstance(data, dict)
    data["wait"] = [{"invoice_type": "D0501", "create_date": _now_epoch()}]
    transport = _ScriptedTransport(dict(_QUERY_INVOICE_OPEN_1000), queued)
    with pytest.raises(AmegoTransportError, match="折讓"):
        await EInvoiceService(db_session).send_via_amego(
            store_id, f0501.id, client=_client(transport)
        )
    assert not any(url.endswith("/json/f0501") for url, _ in transport.calls)
    await db_session.refresh(f0501)
    assert f0501.status is UploadStatus.PENDING
    assert f0501.last_error is not None and "折讓" in f0501.last_error
    assert (await _invoice(db_session, sale_id)).status is InvoiceStatus.VOID_PENDING

    # 平台處理完 → 下一次送出就成功
    await _send(
        db_session,
        store_id,
        f0501,
        dict(_QUERY_INVOICE_OPEN_1000),
        _allowance_voided(286, 14),
        {"code": 0, "msg": ""},
    )
    assert (await _invoice(db_session, sale_id)).status is InvoiceStatus.VOID


async def test_rejected_allowance_void_can_be_retried_and_then_voids_the_invoice(
    db_session: AsyncSession,
) -> None:
    """G0501 被平台退回：發票停在作廢中，店員要能重送 G0501（Codex 第一輪 high）。

    「作廢中不可再送折讓」只擋開立折讓（G0401）；作廢折讓正是作廢流程的一步，擋了就卡死。
    """
    store_id, clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)
    [g0401] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE)
    await _send(
        db_session, store_id, g0401, dict(_QUERY_ALLOWANCE_NOT_FOUND), {"code": 0, "msg": ""}
    )
    await _void_sale(db_session, store_id, clerk_id, sale_id)
    [g0501] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE_VOID)
    await _send(db_session, store_id, g0501, _allowance_exists(286, 14), {"code": 1, "msg": "拒"})
    await db_session.refresh(g0501)
    assert g0501.status is UploadStatus.FAILED
    assert (await _invoice(db_session, sale_id)).status is InvoiceStatus.VOID_PENDING

    retried = await EInvoiceService(db_session).retry(store_id, g0501.id)
    assert retried.status is UploadStatus.PENDING
    await _send(db_session, store_id, retried, _allowance_exists(286, 14), {"code": 0, "msg": ""})
    [allowance] = await _allowances(db_session, sale_id)
    assert allowance.voided is True
    assert len(_pending(await _queue(db_session, store_id), EInvoiceAction.VOID)) == 1


async def test_allowance_issue_still_cannot_be_retried_while_invoice_is_voiding(
    db_session: AsyncSession,
) -> None:
    """既有規則不變：發票作廢中，失敗的開立折讓（G0401）仍不可重送（不可既作廢又折讓）。"""
    store_id, _clerk_id, sale_id = await _mixed_sale(db_session)
    await _issue(db_session, store_id)
    [g0401] = _pending(await _queue(db_session, store_id), EInvoiceAction.ALLOWANCE)
    await _send(
        db_session, store_id, g0401, dict(_QUERY_ALLOWANCE_NOT_FOUND), {"code": 1, "msg": "拒"}
    )
    invoice = await _invoice(db_session, sale_id)
    invoice.status = InvoiceStatus.VOID_PENDING
    invoice.void_reason = InvoiceVoidReason.SALE_VOID
    await db_session.flush()
    with pytest.raises(EInvoiceQueueNotRetryable):
        await EInvoiceService(db_session).retry(store_id, g0401.id)
