"""開立發票的 Amego 訂單編號：建立時隨機產生並持久化；撞號時送出流程自動換新編號。

2026-10-01 正式機：資料庫在 9/18 重建過，銷售 #4、#5 的 OrderId `S1-4`／`S1-5` 撞上平台上
重建前的測試發票（11 元、已作廢）。對帳守衛正確地擋下，卻沒有任何出路——同一個編號永遠
查得到那張舊發票。店主裁示「盡可能不要讓人工介入發票系統」，這裡守住：

1. 新發票的編號帶隨機段，資料庫重建後不會再跟平台舊紀錄同號；送出、對帳一律用
   **存下來的那個編號**，不再由 (store, sale) 重新推導。
2. 撞號（平台證實該編號底下是別筆）時自動處理：平台 OrderId 唯一，別筆占著就代表本筆從未
   成立。未作廢 → 換新編號、以新編號再對帳一次後送出；已作廢 → 取消開立、不開發票。
3. **別筆的發票號碼絕不會被記成本筆的**（原守衛的核心不變量，照舊成立）。
4. 只有「證實是別筆」才自動處理；回應曖昧或新編號又撞，一律停下待對帳，不循環換號。
"""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.einvoice.amego import parse_query_issued
from app.modules.einvoice.models import EInvoiceUploadQueue, Invoice
from app.modules.einvoice.service import EInvoiceService
from app.modules.sales.service import SalesService
from app.shared.enums import InvoiceStatus, SaleInvoiceStatus, UploadStatus
from app.shared.exceptions import AmegoIdentifierCollision, AmegoTransportError
from tests.integration.test_einvoice_amego_send import (
    _QUERY_NOT_FOUND,
    _checkout,
    _client,
    _issue_ok_transport,
    _issue_queue_id,
    _ScriptedTransport,
    _seed,
)

_ORDER_ID_RE = re.compile(r"S\d+-\d+-[23456789A-HJ-NP-Z]{8}")

# 本筆開立成功時平台配的號碼——刻意與撞號那張（FX27312200）不同，才驗得出記的是哪張。
_F0401_OK_OURS = {
    "code": 0,
    "msg": "",
    "invoice_number": "AB00002222",
    "invoice_time": 1783766130,
    "random_number": "5975",
    "barcode": "11507AB000022225975",
    "qrcode_left": "AB000022221150711...",
    "qrcode_right": "**品名...",
}


def _platform_record(*, total: int, create_date: int | None = None) -> dict[str, object]:
    """invoice_query 查到一張發票。種子交易總額 1050；別的金額＝別筆交易的舊紀錄。"""
    data: dict[str, object] = {
        "invoice_number": "FX27312200",
        "invoice_type": "C0401",
        "invoice_date": "20260918",
        "invoice_time": "17:46:26",
        "random_number": "1234",
        "invoice_status": 99,
        "total_amount": total,
    }
    if create_date is not None:
        data["create_date"] = create_date
    return {"code": 0, "msg": "", "data": data}


_BEFORE_REBUILD = 1_789_725_986  # 2026-09-18 17:46，資料庫重建前


def _collided() -> dict[str, object]:
    """平台上同編號的是重建前的 11 元測試發票（正式機實際查到的形狀：有建檔時間）。"""
    return _platform_record(total=11, create_date=_BEFORE_REBUILD)


def _sent_order_id(transport: _ScriptedTransport, call: int, key: str) -> str:
    return cast("str", json.loads(transport.calls[call][1]["data"])[key])


async def _invoice_for(session: AsyncSession, sale_id: int) -> Invoice:
    invoice = await session.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
    assert invoice is not None
    return invoice


async def _sale_with_pending_issue(session: AsyncSession) -> tuple[int, int, int, int]:
    """回 (store_id, clerk_id, sale_id, issue_queue_id)。"""
    store_id, clerk_id, code = await _seed(session)
    sale_id = await _checkout(session, store_id, clerk_id, code)
    queue_id = await _issue_queue_id(EInvoiceService(session), store_id)
    return store_id, clerk_id, sale_id, queue_id


async def _claimed_but_unsent(session: AsyncSession) -> tuple[int, int, int, int]:
    """正式機 #7／#8 的形狀：已認領（凍結 payload）、PENDING、從未成功送出。"""
    store_id, clerk_id, sale_id, queue_id = await _sale_with_pending_issue(session)
    with pytest.raises(AmegoTransportError):
        await EInvoiceService(session).send_via_amego(
            store_id,
            queue_id,
            client=_client(_ScriptedTransport(AmegoTransportError("ConnectTimeout"))),
        )
    item = await session.get(EInvoiceUploadQueue, queue_id)
    assert item is not None and item.amego_payload is not None
    return store_id, clerk_id, sale_id, queue_id


# ── 新發票的編號 ──


async def test_new_invoice_gets_random_order_id_and_f0401_sends_that_exact_id(
    db_session: AsyncSession,
) -> None:
    store_id, _clerk, sale_id, queue_id = await _sale_with_pending_issue(db_session)
    invoice = await _invoice_for(db_session, sale_id)

    assert _ORDER_ID_RE.fullmatch(invoice.platform_order_id)
    assert invoice.platform_order_id.startswith(f"S{store_id}-{sale_id}-")

    transport = _issue_ok_transport()
    await EInvoiceService(db_session).send_via_amego(store_id, queue_id, client=_client(transport))
    assert _sent_order_id(transport, 0, "order_id") == invoice.platform_order_id
    assert _sent_order_id(transport, 1, "OrderId") == invoice.platform_order_id


# ── 守衛把撞號標成可辨識的例外 ──


def test_older_record_with_other_amount_is_reported_as_identifier_collision() -> None:
    with pytest.raises(AmegoIdentifierCollision):
        parse_query_issued(
            _collided(), expect_total=Decimal(1050), expect_not_before=datetime.now(tz=UTC)
        )


def test_amount_mismatch_alone_is_not_proof_of_collision() -> None:
    """只有金額不符、沒有「建於本筆之前」的時間證據：可能是本筆但平台金額口徑不同，
    自動換號會重複開立——不算撞號，照舊停下待對帳（code-reviewer M1）。"""
    for record in (
        _platform_record(total=11),  # 沒帶建檔時間
        _platform_record(total=11, create_date=int(datetime.now(tz=UTC).timestamp())),
    ):
        with pytest.raises(AmegoTransportError) as excinfo:
            parse_query_issued(
                record, expect_total=Decimal(1050), expect_not_before=datetime.now(tz=UTC)
            )
        assert not isinstance(excinfo.value, AmegoIdentifierCollision)


def test_record_older_than_our_message_is_reported_as_identifier_collision() -> None:
    with pytest.raises(AmegoIdentifierCollision):
        parse_query_issued(
            _platform_record(total=1050, create_date=1_789_725_986),  # 2026-09-18
            expect_total=Decimal(1050),
            expect_not_before=datetime.now(tz=UTC),
        )


def test_ambiguous_answer_is_not_a_collision() -> None:
    with pytest.raises(AmegoTransportError) as excinfo:
        parse_query_issued(
            {"code": 500, "msg": "x"},
            expect_total=Decimal(1050),
            expect_not_before=datetime.now(tz=UTC),
        )
    assert not isinstance(excinfo.value, AmegoIdentifierCollision)


# ── 撞號自動處理 ──


async def test_collision_rotates_order_id_and_issues_under_the_new_one(
    db_session: AsyncSession,
) -> None:
    store_id, _clerk, sale_id, queue_id = await _sale_with_pending_issue(db_session)
    invoice = await _invoice_for(db_session, sale_id)
    old_order_id = invoice.platform_order_id

    transport = _ScriptedTransport(_collided(), dict(_QUERY_NOT_FOUND), dict(_F0401_OK_OURS))
    item = await EInvoiceService(db_session).send_via_amego(
        store_id, queue_id, client=_client(transport)
    )

    assert item.status is UploadStatus.UPLOADED
    await db_session.refresh(invoice)
    new_order_id = invoice.platform_order_id
    assert new_order_id != old_order_id and _ORDER_ID_RE.fullmatch(new_order_id)
    assert _sent_order_id(transport, 0, "order_id") == old_order_id
    assert _sent_order_id(transport, 1, "order_id") == new_order_id  # 新編號也先對帳
    assert _sent_order_id(transport, 2, "OrderId") == new_order_id
    assert invoice.status is InvoiceStatus.ISSUED
    assert invoice.invoice_no == "AB00002222"  # 絕不是撞號那張 FX27312200


async def test_claimed_legacy_row_is_refrozen_with_the_new_order_id(
    db_session: AsyncSession,
) -> None:
    """已認領的凍結 payload 一起換號、checksum 重算，送出的內容與落庫的一致。"""
    store_id, _clerk, sale_id, queue_id = await _claimed_but_unsent(db_session)

    transport = _ScriptedTransport(_collided(), dict(_QUERY_NOT_FOUND), dict(_F0401_OK_OURS))
    item = await EInvoiceService(db_session).send_via_amego(
        store_id, queue_id, client=_client(transport)
    )

    assert item.status is UploadStatus.UPLOADED
    invoice = await _invoice_for(db_session, sale_id)
    assert item.amego_payload is not None
    assert json.loads(item.amego_payload)["OrderId"] == invoice.platform_order_id
    assert transport.calls[2][1]["data"] == item.amego_payload


async def test_rotation_is_audited_as_system_action(db_session: AsyncSession) -> None:
    store_id, _clerk, sale_id, queue_id = await _sale_with_pending_issue(db_session)
    invoice = await _invoice_for(db_session, sale_id)
    old_order_id = invoice.platform_order_id

    await EInvoiceService(db_session).send_via_amego(
        store_id,
        queue_id,
        client=_client(
            _ScriptedTransport(_collided(), dict(_QUERY_NOT_FOUND), dict(_F0401_OK_OURS))
        ),
    )

    log = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "ROTATE_EINVOICE_ORDER_ID")
    )
    assert log is not None
    assert log.actor_user_id is None
    assert log.entity_id == str(invoice.id)
    assert log.before == {"platform_order_id": old_order_id}
    await db_session.refresh(invoice)
    assert log.after == {"platform_order_id": invoice.platform_order_id}


async def test_collision_on_voided_sale_cancels_issue_instead_of_issuing(
    db_session: AsyncSession,
) -> None:
    """交易已作廢：本筆從未在平台成立 → 直接收掉、不開立（正式機 #4 的情形）。

    換號再送會為作廢交易開出一張真發票，再靠 F0501 收拾——稅務上平白多一開一廢。
    """
    store_id, clerk_id, sale_id, queue_id = await _claimed_but_unsent(db_session)
    sales = SalesService(db_session)
    sale = await sales.get_sale(store_id, sale_id)
    assert sale is not None
    await sales.void_sale(sale, clerk_id)
    invoice = await _invoice_for(db_session, sale_id)
    assert invoice.status is InvoiceStatus.VOID_PENDING
    old_order_id = invoice.platform_order_id

    transport = _ScriptedTransport(_collided())
    item = await EInvoiceService(db_session).send_via_amego(
        store_id, queue_id, client=_client(transport)
    )

    assert len(transport.calls) == 1  # 只查詢，沒有送出任何開立
    assert item.status is UploadStatus.CANCELLED
    await db_session.refresh(invoice)
    assert cast("InvoiceStatus", invoice.status) is InvoiceStatus.VOID
    assert invoice.invoice_no is None
    assert invoice.platform_order_id == old_order_id  # 不開立就不必換號
    await db_session.refresh(sale)
    assert sale.invoice_status is SaleInvoiceStatus.NOT_ISSUED


async def test_second_collision_on_new_id_stops_instead_of_looping(
    db_session: AsyncSession,
) -> None:
    """新編號又撞（實際上不會發生）：停下待對帳，不循環換號、不送出。"""
    store_id, _clerk, sale_id, queue_id = await _sale_with_pending_issue(db_session)

    transport = _ScriptedTransport(_collided(), _collided())
    with pytest.raises(AmegoIdentifierCollision):
        await EInvoiceService(db_session).send_via_amego(
            store_id, queue_id, client=_client(transport)
        )

    assert len(transport.calls) == 2  # 兩次查詢、零次開立
    item = await db_session.get(EInvoiceUploadQueue, queue_id)
    assert item is not None
    await db_session.refresh(item)
    assert item.status is UploadStatus.PENDING
    assert item.last_error is not None and "識別碼重號" in item.last_error
    invoice = await _invoice_for(db_session, sale_id)
    assert invoice.status is InvoiceStatus.PENDING
    # 換過的編號與凍結內容一起落庫（不會一半新一半舊），下次送出以新編號對帳
    assert item.amego_payload is not None
    assert json.loads(item.amego_payload)["OrderId"] == invoice.platform_order_id


async def test_record_that_is_ours_is_recovered_not_rotated(db_session: AsyncSession) -> None:
    """平台上那張就是本筆（同額、建於本訊息之後）：補記、不換號、不重送。"""
    store_id, _clerk, sale_id, queue_id = await _claimed_but_unsent(db_session)
    invoice = await _invoice_for(db_session, sale_id)
    before = invoice.platform_order_id
    ours = _platform_record(total=1050, create_date=int(datetime.now(tz=UTC).timestamp()))

    transport = _ScriptedTransport(ours)
    item = await EInvoiceService(db_session).send_via_amego(
        store_id, queue_id, client=_client(transport)
    )

    assert item.status is UploadStatus.UPLOADED
    assert len(transport.calls) == 1
    await db_session.refresh(invoice)
    assert invoice.platform_order_id == before
    assert invoice.invoice_no == "FX27312200"


async def test_amount_mismatch_without_time_evidence_stays_stuck(
    db_session: AsyncSession,
) -> None:
    store_id, _clerk, sale_id, queue_id = await _sale_with_pending_issue(db_session)
    invoice = await _invoice_for(db_session, sale_id)
    before = invoice.platform_order_id

    transport = _ScriptedTransport(_platform_record(total=11))
    with pytest.raises(AmegoTransportError) as excinfo:
        await EInvoiceService(db_session).send_via_amego(
            store_id, queue_id, client=_client(transport)
        )

    assert not isinstance(excinfo.value, AmegoIdentifierCollision)
    assert len(transport.calls) == 1  # 沒換號、沒送出
    await db_session.refresh(invoice)
    assert invoice.platform_order_id == before
    assert invoice.status is InvoiceStatus.PENDING


async def test_resend_after_restoring_pre_rotation_backup_recovers_instead_of_reissuing(
    db_session: AsyncSession,
) -> None:
    """換號並開立成功後，資料庫從**換號前**的備份還原，再送一次：必須查到本筆、補記，
    不可再換一組新號送出（那會是同一筆交易的第二張發票，code-reviewer H1）。"""
    store_id, _clerk, sale_id, queue_id = await _claimed_but_unsent(db_session)
    invoice = await _invoice_for(db_session, sale_id)
    item = await db_session.get(EInvoiceUploadQueue, queue_id)
    assert item is not None
    sale = await SalesService(db_session).get_sale(store_id, sale_id)
    assert sale is not None
    backup_order_id = invoice.platform_order_id
    backup_payload = item.amego_payload
    backup_sha = item.xml_sha256
    backup_sale_invoice_status = sale.invoice_status

    first = _ScriptedTransport(_collided(), dict(_QUERY_NOT_FOUND), dict(_F0401_OK_OURS))
    await EInvoiceService(db_session).send_via_amego(store_id, queue_id, client=_client(first))
    await db_session.refresh(invoice)
    issued_under = invoice.platform_order_id
    assert invoice.status is InvoiceStatus.ISSUED

    # ── 模擬還原：發票、佇列列、銷售回到換號前（已認領、待開立）的樣子 ──
    invoice.status = InvoiceStatus.PENDING
    for field in (
        "invoice_no",
        "invoice_date",
        "invoice_time",
        "random_number",
        "barcode_text",
        "qrcode_left",
        "qrcode_right",
    ):
        setattr(invoice, field, None)
    invoice.platform_order_id = backup_order_id
    await db_session.refresh(item)
    item.status = UploadStatus.PENDING
    item.uploaded_at = None
    item.amego_payload = backup_payload
    item.xml_sha256 = backup_sha
    await db_session.refresh(sale)
    sale.invoice_status = backup_sale_invoice_status
    await db_session.commit()

    ours_under_rotated_id = _platform_record(
        total=1050, create_date=int(datetime.now(tz=UTC).timestamp())
    )
    data = ours_under_rotated_id["data"]
    assert isinstance(data, dict)
    data["invoice_number"] = "AB00002222"
    again = _ScriptedTransport(_collided(), ours_under_rotated_id)
    item = await EInvoiceService(db_session).send_via_amego(
        store_id, queue_id, client=_client(again)
    )

    assert [c[0].rsplit("/json/", 1)[-1] for c in again.calls] == [
        "invoice_query",
        "invoice_query",
    ]  # 沒有第二次 f0401
    assert _sent_order_id(again, 1, "order_id") == issued_under  # 算回同一個新編號
    assert item.status is UploadStatus.UPLOADED
    await db_session.refresh(invoice)
    assert invoice.platform_order_id == issued_under
    assert invoice.invoice_no == "AB00002222"
