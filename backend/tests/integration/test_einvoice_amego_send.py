"""einvoice × Amego 送單整合測試（docs/24 A1/A2/A3）。

結帳（einvoice_enabled）建 PENDING 發票＋F0401 佇列（既有）；`send_via_amego` 把佇列列
送 Amego：f0401 成功 → 發票 ISSUED＋字軌/隨機碼/條碼QR 內容、佇列 UPLOADED、sale 同步；
失敗 → FAILED 可重送。傳輸中斷（結果未知）→ 佇列維持 PENDING（已認領），下次送先以
invoice_query 對帳。作廢（F0501）與折讓（G0401）走同一出口。
"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.service import CashDrawerService
from app.modules.einvoice.amego import AmegoClient
from app.modules.einvoice.models import EInvoiceUploadQueue, Invoice
from app.modules.einvoice.service import EInvoiceService
from app.modules.inventory.service import InventoryService
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import InvoiceInfoInput, SaleLineInput
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    EInvoiceAction,
    Grade,
    InvoiceStatus,
    OwnershipType,
    SaleInvoiceStatus,
    SaleLineType,
    UploadStatus,
    UserRole,
)
from app.shared.exceptions import AmegoTransportError, EInvoiceQueueNotDroppable

# f0401 成功回應樣板（doc 回應欄位；invoice_time 為 Unix 秒）。
_F0401_OK = {
    "code": 0,
    "msg": "",
    "invoice_number": "AB00001111",
    "invoice_time": 1783766130,  # 2026-07-11 Asia/Taipei
    "random_number": "5975",
    "barcode": "11507AB000011115975",
    "qrcode_left": "AB000011111150711...",
    "qrcode_right": "**品名...",
}


class _ScriptedTransport:
    """依呼叫順序回放回應；記錄每次 (endpoint, form)。回應可為 dict 或 Exception。"""

    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def post_form(self, url: str, form: dict[str, str]) -> dict[str, object]:
        self.calls.append((url, form))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        assert isinstance(result, dict)
        return result


def _client(transport: _ScriptedTransport) -> AmegoClient:
    return AmegoClient(
        seller_tax_id="12345678",
        app_key="test-key",
        transport=transport,
        base_url="https://invoice-api.amego.tw",
    )


async def _seed(session: AsyncSession) -> tuple[int, int, str]:
    store = Store(name="門市", tax_id="12345678")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    session.add(StoreSettings(store_id=store.id, einvoice_enabled=True))
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("1000"))
    item = await InventoryService(session).create_serialized_item(
        store.id,
        item_code="SN-1",
        name="相機",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal(1050),
        acquisition_cost=Decimal(500),
    )
    return store.id, clerk.id, item.item_code


async def _checkout(
    session: AsyncSession,
    store_id: int,
    clerk_id: int,
    code: str,
    *,
    invoice_info: InvoiceInfoInput | None = None,
) -> int:
    sale = await SalesService(session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
        invoice_info=invoice_info,
    )
    return sale.id


async def _issue_queue_id(svc: EInvoiceService, store_id: int) -> int:
    return next(
        i.id for i in await svc.list_queue(store_id) if i.action is EInvoiceAction.ISSUE
    )


async def test_send_f0401_success_issues_invoice(db_session: AsyncSession) -> None:
    store_id, clerk_id, code = await _seed(db_session)
    sale_id = await _checkout(db_session, store_id, clerk_id, code)
    svc = EInvoiceService(db_session)
    queue_id = await _issue_queue_id(svc, store_id)
    transport = _ScriptedTransport(dict(_F0401_OK))

    item = await svc.send_via_amego(store_id, queue_id, client=_client(transport))

    assert item.status is UploadStatus.UPLOADED
    invoice = await db_session.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
    assert invoice is not None
    assert invoice.status is InvoiceStatus.ISSUED
    assert invoice.invoice_no == "AB00001111"
    assert invoice.random_number == "5975"
    assert invoice.invoice_date is not None and invoice.invoice_time is not None
    assert invoice.barcode_text == "11507AB000011115975"
    assert invoice.qrcode_left and invoice.qrcode_right
    sale = await SalesService(db_session).get_sale(store_id, sale_id)
    assert sale is not None and sale.invoice_status is SaleInvoiceStatus.ISSUED
    # 送出的 f0401 內容：B2C 制式買方、含稅金額
    _url, form = transport.calls[0]
    assert _url.endswith("/json/f0401")
    import json as _json

    data = _json.loads(form["data"])
    assert data["OrderId"] == f"S{store_id}-{sale_id}"
    assert data["BuyerIdentifier"] == "0000000000"
    assert data["SalesAmount"] == 1050
    assert data["TaxAmount"] == 0
    assert data["TotalAmount"] == 1050


async def test_send_f0401_b2b_uses_buyer_tax_id_and_split(db_session: AsyncSession) -> None:
    store_id, clerk_id, code = await _seed(db_session)
    sale_id = await _checkout(
        db_session,
        store_id,
        clerk_id,
        code,
        invoice_info=InvoiceInfoInput(buyer_tax_id="04595257", buyer_name="範例公司"),
    )
    svc = EInvoiceService(db_session)
    queue_id = await _issue_queue_id(svc, store_id)
    transport = _ScriptedTransport(dict(_F0401_OK))

    await svc.send_via_amego(store_id, queue_id, client=_client(transport))

    import json as _json

    data = _json.loads(transport.calls[0][1]["data"])
    assert data["BuyerIdentifier"] == "04595257"
    assert data["BuyerName"] == "範例公司"
    assert data["SalesAmount"] == 1000  # round(1050/1.05)
    assert data["TaxAmount"] == 50
    assert data["TotalAmount"] == 1050
    invoice = await db_session.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
    assert invoice is not None and invoice.invoice_type.value == "B2B"


async def test_send_f0401_api_failure_marks_failed_then_retry(db_session: AsyncSession) -> None:
    store_id, clerk_id, code = await _seed(db_session)
    sale_id = await _checkout(db_session, store_id, clerk_id, code)
    svc = EInvoiceService(db_session)
    queue_id = await _issue_queue_id(svc, store_id)
    transport = _ScriptedTransport({"code": 3021, "msg": "統一編號格式錯誤"})

    item = await svc.send_via_amego(store_id, queue_id, client=_client(transport))

    assert item.status is UploadStatus.FAILED
    assert item.last_error is not None and "3021" not in (item.xml_path or "")
    invoice = await db_session.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
    assert invoice is not None and invoice.status is InvoiceStatus.PENDING  # 未開立、可重試

    # retry → PENDING（世代 +1），再送成功
    await svc.retry(store_id, queue_id)
    transport2 = _ScriptedTransport(dict(_F0401_OK))
    item2 = await svc.send_via_amego(store_id, queue_id, client=_client(transport2))
    assert item2.status is UploadStatus.UPLOADED
    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.ISSUED


async def test_transport_error_leaves_claimed_pending_then_query_reconciles(
    db_session: AsyncSession,
) -> None:
    """網路中斷（結果未知）：佇列維持 PENDING＋已認領；下次送先 invoice_query——
    平台已有 → 以查詢結果補開立（無條碼/QR 內容）、不重送 f0401。"""
    store_id, clerk_id, code = await _seed(db_session)
    sale_id = await _checkout(db_session, store_id, clerk_id, code)
    svc = EInvoiceService(db_session)
    queue_id = await _issue_queue_id(svc, store_id)

    transport = _ScriptedTransport(AmegoTransportError("Amego API 呼叫失敗：ConnectTimeout"))
    with pytest.raises(AmegoTransportError):
        await svc.send_via_amego(store_id, queue_id, client=_client(transport))
    item = await db_session.get(EInvoiceUploadQueue, queue_id)
    assert item is not None
    await db_session.refresh(item)
    assert item.status is UploadStatus.PENDING
    assert item.xml_path is not None  # 已認領（可能已送達平台）

    transport2 = _ScriptedTransport(
        {
            "code": 0,
            "msg": "",
            "data": {
                "invoice_number": "AB00001111",
                "invoice_date": "20260711",
                "invoice_time": "12:34:56",
                "random_number": "5975",
            },
        }
    )
    item2 = await svc.send_via_amego(store_id, queue_id, client=_client(transport2))
    assert transport2.calls[0][0].endswith("/json/invoice_query")
    assert len(transport2.calls) == 1  # 不重送 f0401
    assert item2.status is UploadStatus.UPLOADED
    invoice = await db_session.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
    assert invoice is not None
    assert invoice.status is InvoiceStatus.ISSUED
    assert invoice.invoice_no == "AB00001111"
    assert invoice.barcode_text is None  # 查詢不回條碼內容 → 證明聯不可印


async def test_claimed_pending_query_not_found_resends_f0401(db_session: AsyncSession) -> None:
    store_id, clerk_id, code = await _seed(db_session)
    await _checkout(db_session, store_id, clerk_id, code)
    svc = EInvoiceService(db_session)
    queue_id = await _issue_queue_id(svc, store_id)

    transport = _ScriptedTransport(AmegoTransportError("Amego API 呼叫失敗：ConnectTimeout"))
    with pytest.raises(AmegoTransportError):
        await svc.send_via_amego(store_id, queue_id, client=_client(transport))

    transport2 = _ScriptedTransport({"code": 9001, "msg": "查無資料"}, dict(_F0401_OK))
    item = await svc.send_via_amego(store_id, queue_id, client=_client(transport2))
    assert transport2.calls[0][0].endswith("/json/invoice_query")
    assert transport2.calls[1][0].endswith("/json/f0401")
    assert item.status is UploadStatus.UPLOADED


async def test_void_issued_invoice_sends_f0501(db_session: AsyncSession) -> None:
    store_id, clerk_id, code = await _seed(db_session)
    sale_id = await _checkout(db_session, store_id, clerk_id, code)
    svc = EInvoiceService(db_session)
    queue_id = await _issue_queue_id(svc, store_id)
    await svc.send_via_amego(
        store_id, queue_id, client=_client(_ScriptedTransport(dict(_F0401_OK)))
    )

    sales = SalesService(db_session)
    sale = await sales.get_sale(store_id, sale_id)
    assert sale is not None
    await sales.void_sale(sale, clerk_id)
    invoice = await db_session.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
    assert invoice is not None and invoice.status is InvoiceStatus.VOID_PENDING

    void_queue_id = next(
        i.id
        for i in await svc.list_queue(store_id)
        if i.action is EInvoiceAction.VOID and i.status is UploadStatus.PENDING
    )
    transport = _ScriptedTransport({"code": 0, "msg": ""})
    item = await svc.send_via_amego(store_id, void_queue_id, client=_client(transport))

    assert item.status is UploadStatus.UPLOADED
    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.VOID
    import json as _json

    data = _json.loads(transport.calls[0][1]["data"])
    assert data == [{"CancelInvoiceNumber": "AB00001111"}]


async def test_return_allowance_sends_g0401(db_session: AsyncSession) -> None:
    store_id, clerk_id, code = await _seed(db_session)
    sale_id = await _checkout(db_session, store_id, clerk_id, code)
    svc = EInvoiceService(db_session)
    queue_id = await _issue_queue_id(svc, store_id)
    await svc.send_via_amego(
        store_id, queue_id, client=_client(_ScriptedTransport(dict(_F0401_OK)))
    )

    sales = SalesService(db_session)
    lines = await sales.get_lines(sale_id)
    await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale_id,
        lines=[ReturnLineInput(sale_line_id=lines[0].id, qty=1)],
        reason="測試退貨",
        actor_user_id=clerk_id,
        idempotency_key="amego-return-1",
    )
    allowance_queue_id = next(
        i.id
        for i in await svc.list_queue(store_id)
        if i.action is EInvoiceAction.ALLOWANCE and i.status is UploadStatus.PENDING
    )
    transport = _ScriptedTransport({"code": 0, "msg": ""})
    item = await svc.send_via_amego(store_id, allowance_queue_id, client=_client(transport))

    assert item.status is UploadStatus.UPLOADED
    import json as _json

    data = _json.loads(transport.calls[0][1]["data"])
    assert isinstance(data, list) and len(data) == 1
    entry = data[0]
    assert entry["AllowanceType"] == 2  # 賣方折讓證明通知單（114 年起賣方開立）
    assert entry["ProductItem"][0]["OriginalInvoiceNumber"] == "AB00001111"
    # 折讓金額為未稅口徑：1050 → 未稅 1000 / 稅 50
    assert entry["TaxAmount"] == 50
    assert entry["TotalAmount"] == 1000
    sale = await sales.get_sale(store_id, sale_id)
    assert sale is not None and sale.invoice_status is SaleInvoiceStatus.ALLOWANCE


async def test_send_rejects_non_pending(db_session: AsyncSession) -> None:
    store_id, clerk_id, code = await _seed(db_session)
    await _checkout(db_session, store_id, clerk_id, code)
    svc = EInvoiceService(db_session)
    queue_id = await _issue_queue_id(svc, store_id)
    await svc.send_via_amego(
        store_id, queue_id, client=_client(_ScriptedTransport(dict(_F0401_OK)))
    )

    with pytest.raises(EInvoiceQueueNotDroppable):
        await svc.send_via_amego(
            store_id, queue_id, client=_client(_ScriptedTransport(dict(_F0401_OK)))
        )
