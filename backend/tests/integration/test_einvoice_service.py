"""einvoice service 整合測試：待開立/拋檔/回執/重送/折讓的狀態機與守衛（T13 infra）。

對 CLAUDE.md §6（稅在總額層級推算一次）與 docs/18 §7 的佇列狀態機；狀態語意誠實：
結帳建 PENDING 發票，唯 ProcessResult 成功才 ISSUED。真 Postgres、外層交易 rollback 隔離。
"""

import hashlib
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import NoReturn

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import split_tax_inclusive
from app.modules.einvoice.dropper import EInvoiceDropper
from app.modules.einvoice.models import (
    EInvoiceResultEvent,
    EInvoiceUploadQueue,
    Invoice,
)
from app.modules.einvoice.serializer import DeferredXmlSerializer
from app.modules.einvoice.service import EInvoiceService
from app.modules.sales.models import Sale
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    EInvoiceAction,
    EInvoiceMessageType,
    InvoiceStatus,
    InvoiceType,
    UploadStatus,
    UserRole,
)
from app.shared.exceptions import (
    AllowanceExceedsInvoice,
    DuplicateAllowanceForReturn,
    EInvoiceDropError,
    EInvoiceQueueItemNotFound,
    EInvoiceQueueNotDroppable,
    EInvoiceQueueNotRetryable,
    EInvoiceResultConflict,
    EInvoiceResultNotApplicable,
    EInvoiceSerializerNotReady,
    InvoiceIncompleteForIssue,
    InvoiceNotIssued,
)

TAX_RATE = Decimal("0.05")


class _FakeSerializer:
    """測試用序列化器：回傳固定 bytes（確定性），驗證拋檔機制不被 NotReady 阻擋。"""

    def serialize_invoice(self, invoice: Invoice, message_type: EInvoiceMessageType) -> bytes:
        return b"<Invoice/>"

    def serialize_allowance(self, allowance: object, message_type: EInvoiceMessageType) -> bytes:
        return b"<Allowance/>"


class _AltSerializer(_FakeSerializer):
    """輸出不同內容的序列化器：模擬非確定性/內容漂移（恢復時應被 sha 守衛拒絕）。"""

    def serialize_invoice(self, invoice: Invoice, message_type: EInvoiceMessageType) -> bytes:
        return b"<Invoice mutated/>"


class _CrashAfterWriteDropper(EInvoiceDropper):
    """寫檔成功後立刻 crash：模擬「檔案已曝光、確認（dropped_at）未落庫」的中斷窗口。"""

    def drop(self, message_type: EInvoiceMessageType, filename: str, payload: bytes) -> NoReturn:
        super().drop(message_type, filename, payload)
        raise RuntimeError("simulated crash after file write")


async def _seed_sale(session: AsyncSession, *, total: Decimal = Decimal(1050)) -> tuple[int, int]:
    """建 store + clerk + 一筆 sale；回 (store_id, sale_id)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    net, tax = split_tax_inclusive(total, TAX_RATE)
    sale = Sale(
        store_id=store.id,
        clerk_user_id=clerk.id,
        subtotal=Decimal(net),
        tax=Decimal(tax),
        total=total,
    )
    session.add(sale)
    await session.flush()
    return store.id, sale.id


async def _first_queue_id(svc: EInvoiceService, store_id: int) -> int:
    items = await svc.list_queue(store_id)
    return items[0].id


async def _fill_issue_fields(session: AsyncSession, invoice: Invoice) -> None:
    """模擬配號/序列化階段填入開立必要欄位（M1：ISSUED 前字軌/日期/時間/隨機碼須齊備）。"""
    invoice.invoice_no = "AB12345678"
    invoice.invoice_date = date(2026, 7, 1)
    invoice.invoice_time = "12:34:56"
    invoice.random_number = "1234"
    await session.flush()


async def _issue_and_accept(
    session: AsyncSession, svc: EInvoiceService, store_id: int, sale_id: int, tmp_path: Path
) -> Invoice:
    """走完整開立流程到 ISSUED：建 PENDING → 填必要欄位 → 拋檔 → ProcessResult 成功。"""
    invoice = await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    await _fill_issue_fields(session, invoice)
    queue_id = await _first_queue_id(svc, store_id)
    await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await svc.record_result(store_id, queue_id, success=True, status_code="0000")
    return await svc.get_invoice(store_id, invoice.id)


# ── 建立（PENDING，非 ISSUED）──


async def test_create_pending_invoice_is_pending_not_issued(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)

    invoice = await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )

    assert invoice.status is InvoiceStatus.PENDING  # 尚未平台核可，非「已開立」
    assert invoice.invoice_no is None  # 字軌配號 deferred
    assert invoice.net == Decimal(1000)
    assert invoice.tax == Decimal(50)
    assert invoice.net + invoice.tax == invoice.total  # §6 不差一元

    items = await svc.list_queue(store_id)
    assert len(items) == 1
    assert items[0].action is EInvoiceAction.ISSUE
    assert items[0].message_type is EInvoiceMessageType.F0401
    assert items[0].status is UploadStatus.PENDING


async def test_create_pending_invoice_is_idempotent_per_sale(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)

    first = await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    second = await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )

    assert first.id == second.id
    invoice_count = await db_session.scalar(
        select(func.count()).select_from(Invoice).where(Invoice.sale_id == sale_id)
    )
    queue_count = await db_session.scalar(
        select(func.count())
        .select_from(EInvoiceUploadQueue)
        .where(EInvoiceUploadQueue.store_id == store_id)
    )
    assert invoice_count == 1
    assert queue_count == 1


async def test_b2c_forces_empty_buyer_tax_id(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    invoice = await EInvoiceService(db_session).create_pending_invoice(
        store_id,
        sale_id=sale_id,
        total=Decimal(1050),
        tax_rate=TAX_RATE,
        invoice_type=InvoiceType.B2C,
        buyer_tax_id="12345678",
    )
    assert invoice.buyer_tax_id is None


# ── 拋檔（F4 守衛）──


async def test_drop_pending_writes_file_keeps_pending(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _first_queue_id(svc, store_id)

    item = await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )

    assert item.status is UploadStatus.PENDING  # 拋檔不改上傳狀態
    assert item.xml_path is not None
    assert item.xml_path.endswith(f"F0401-{store_id}-{queue_id}.xml")
    assert item.xml_sha256 == hashlib.sha256(b"<Invoice/>").hexdigest()
    assert item.dropped_at is not None


async def test_drop_pending_is_idempotent_when_already_dropped(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _first_queue_id(svc, store_id)
    dropper = EInvoiceDropper(tmp_path)

    first = await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=dropper
    )
    dropped_at = first.dropped_at
    again = await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=dropper
    )

    assert again.dropped_at == dropped_at  # no-op：不重新拋檔
    src_dir = dropper.src_dir(EInvoiceMessageType.F0401)
    assert len(list(src_dir.iterdir())) == 1  # 只寫一份


async def test_drop_pending_rejects_non_pending(db_session: AsyncSession, tmp_path: Path) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await _issue_and_accept(db_session, svc, store_id, sale_id, tmp_path)  # 佇列已 UPLOADED
    queue_id = await _first_queue_id(svc, store_id)

    with pytest.raises(EInvoiceQueueNotDroppable):
        await svc.drop_pending(
            store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
        )


async def test_drop_pending_rejects_voided_invoice(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _first_queue_id(svc, store_id)
    await svc.void_invoice_for_sale(store_id, sale_id)

    with pytest.raises(EInvoiceQueueNotDroppable):
        await svc.drop_pending(
            store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
        )


async def test_drop_pending_surfaces_serializer_not_ready(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _first_queue_id(svc, store_id)

    with pytest.raises(EInvoiceSerializerNotReady):
        await svc.drop_pending(
            store_id,
            queue_id,
            serializer=DeferredXmlSerializer(),
            dropper=EInvoiceDropper(tmp_path),
        )


async def test_drop_pending_unknown_queue_raises(db_session: AsyncSession, tmp_path: Path) -> None:
    store_id, _sale_id = await _seed_sale(db_session)
    with pytest.raises(EInvoiceQueueItemNotFound):
        await EInvoiceService(db_session).drop_pending(
            store_id, 999999, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
        )


# ── 兩階段拋檔 crash 韌性（Codex adversarial：檔案曝光不可先於持久狀態）──


async def _claim_then_crash(
    db_session: AsyncSession, svc: EInvoiceService, store_id: int, tmp_path: Path
) -> int:
    """把首個佇列列推進到「已認領＋檔案已寫＋確認未落庫」的中斷態；回 queue_id。"""
    queue_id = await _first_queue_id(svc, store_id)
    with pytest.raises(RuntimeError, match="simulated crash"):
        await svc.drop_pending(
            store_id,
            queue_id,
            serializer=_FakeSerializer(),
            dropper=_CrashAfterWriteDropper(tmp_path),
        )
    item = (await svc.list_queue(store_id))[0]
    assert item.xml_path is not None  # 認領已持久（先於檔案曝光 commit）
    assert item.dropped_at is None  # 確認遺失（crash 窗口）
    return queue_id


async def test_crash_after_file_receipt_still_accepted(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """crash 後 DB 說「未確認」但檔案已可能被 Turnkey 撿走 → 回執以「已認領」受理、可收斂。"""
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    invoice = await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    await _fill_issue_fields(db_session, invoice)
    queue_id = await _claim_then_crash(db_session, svc, store_id, tmp_path)

    item = await svc.record_result(store_id, queue_id, success=True, status_code="0000")

    assert item.status is UploadStatus.UPLOADED
    assert (await svc.get_invoice(store_id, invoice.id)).status is InvoiceStatus.ISSUED


async def test_crash_recovery_redrops_same_file_idempotently(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """crash 後重跑 drop_pending：驗 sha 一致 → 覆寫同名檔（不產生第二份）→ 補確認。"""
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _claim_then_crash(db_session, svc, store_id, tmp_path)

    dropper = EInvoiceDropper(tmp_path)
    item = await svc.drop_pending(store_id, queue_id, serializer=_FakeSerializer(), dropper=dropper)

    assert item.dropped_at is not None  # 確認補齊
    src_dir = dropper.src_dir(EInvoiceMessageType.F0401)
    assert len(list(src_dir.iterdir())) == 1  # 檔名確定性 → 覆寫、無第二份


async def test_crash_recovery_rejects_content_drift(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """恢復時重算內容與認領 sha 不符（序列化漂移）→ 拒絕覆寫已可能曝光的檔案。"""
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _claim_then_crash(db_session, svc, store_id, tmp_path)

    with pytest.raises(EInvoiceDropError, match="不符"):
        await svc.drop_pending(
            store_id, queue_id, serializer=_AltSerializer(), dropper=EInvoiceDropper(tmp_path)
        )


async def test_void_with_claimed_f0401_goes_void_pending(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """已認領（檔案可能曝光）但未確認的 F0401，作廢時視為在途 → VOID_PENDING、不 CANCELLED。"""
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    await _claim_then_crash(db_session, svc, store_id, tmp_path)

    voided = await svc.void_invoice_for_sale(store_id, sale_id)

    assert voided is not None
    assert voided.status is InvoiceStatus.VOID_PENDING  # 不可當平台沒收過
    assert (await svc.list_queue(store_id))[0].status is UploadStatus.PENDING  # F0401 保留


# ── 回執（F5 守衛）──


async def test_record_result_process_success_issues_invoice(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    invoice = await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    await _fill_issue_fields(db_session, invoice)  # M1：ISSUED 前必要欄位須齊備
    queue_id = await _first_queue_id(svc, store_id)
    await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )

    item = await svc.record_result(store_id, queue_id, success=True, status_code="0000")

    assert item.status is UploadStatus.UPLOADED
    assert item.uploaded_at is not None
    refreshed = await svc.get_invoice(store_id, invoice.id)
    assert refreshed.status is InvoiceStatus.ISSUED  # 平台核可才正式開立
    event_count = await db_session.scalar(
        select(func.count())
        .select_from(EInvoiceResultEvent)
        .where(EInvoiceResultEvent.queue_id == queue_id)
    )
    assert event_count == 1


async def test_record_result_rejects_issue_without_core_fields(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    # M1：缺字軌/日期/時間/隨機碼的發票，即使平台回成功也不得標為 ISSUED。
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    invoice = await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _first_queue_id(svc, store_id)
    await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )

    with pytest.raises(InvoiceIncompleteForIssue):
        await svc.record_result(store_id, queue_id, success=True)
    # 發票仍 PENDING（未被誤標 ISSUED）。
    assert (await svc.get_invoice(store_id, invoice.id)).status is InvoiceStatus.PENDING


async def test_record_result_requires_dropped_first(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _first_queue_id(svc, store_id)

    with pytest.raises(EInvoiceResultNotApplicable):
        await svc.record_result(store_id, queue_id, success=True)


async def test_summary_result_does_not_change_status(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _first_queue_id(svc, store_id)
    await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )

    item = await svc.record_result(store_id, queue_id, success=True, kind="SUMMARY")

    assert item.status is UploadStatus.PENDING  # SummaryResult 只對帳、不改單筆狀態
    event_count = await db_session.scalar(
        select(func.count())
        .select_from(EInvoiceResultEvent)
        .where(EInvoiceResultEvent.queue_id == queue_id)
    )
    assert event_count == 1


async def test_duplicate_same_outcome_receipt_is_idempotent(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """終態列收到同結果的重複回執（importer 重試常態）→ 冪等接受、事件留檔、狀態不變。"""
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await _issue_and_accept(db_session, svc, store_id, sale_id, tmp_path)  # 已 UPLOADED
    queue_id = await _first_queue_id(svc, store_id)

    item = await svc.record_result(store_id, queue_id, success=True, source_ref="dup-scan")

    assert item.status is UploadStatus.UPLOADED  # 不變
    event_count = await db_session.scalar(
        select(func.count())
        .select_from(EInvoiceResultEvent)
        .where(EInvoiceResultEvent.queue_id == queue_id)
    )
    assert event_count == 2  # 首次核可 + 重複回執都留稽核


async def test_conflicting_late_receipt_keeps_event_and_state(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """終態列收到矛盾回執 → EInvoiceResultConflict；事件留稽核、終態與發票皆不變。"""
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    invoice = await _issue_and_accept(db_session, svc, store_id, sale_id, tmp_path)  # UPLOADED
    queue_id = await _first_queue_id(svc, store_id)

    with pytest.raises(EInvoiceResultConflict):
        await svc.record_result(store_id, queue_id, success=False, message="遲到的失敗回執")

    # 事件已 flush（service 不回滾稽核）；狀態/發票不變。
    event_count = await db_session.scalar(
        select(func.count())
        .select_from(EInvoiceResultEvent)
        .where(EInvoiceResultEvent.queue_id == queue_id)
    )
    assert event_count == 2
    assert (await svc.list_queue(store_id))[0].status is UploadStatus.UPLOADED
    assert (await svc.get_invoice(store_id, invoice.id)).status is InvoiceStatus.ISSUED


async def test_record_result_process_failure_marks_failed(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _first_queue_id(svc, store_id)
    await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )

    item = await svc.record_result(store_id, queue_id, success=False, message="E0001 欄位錯誤")

    assert item.status is UploadStatus.FAILED
    assert item.last_error == "E0001 欄位錯誤"


async def test_record_result_unknown_queue_raises(db_session: AsyncSession) -> None:
    store_id, _sale_id = await _seed_sale(db_session)
    with pytest.raises(EInvoiceQueueItemNotFound):
        await EInvoiceService(db_session).record_result(store_id, 999999, success=True)


# ── 重送 ──


async def test_retry_failed_returns_to_pending_without_new_invoice(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _first_queue_id(svc, store_id)
    await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await svc.record_result(store_id, queue_id, success=False, message="E0001")

    item = await svc.retry(store_id, queue_id)

    assert item.status is UploadStatus.PENDING
    assert item.attempts == 1
    assert item.last_error is None
    assert item.dropped_at is None  # 清痕、可重新拋檔
    invoice_count = await db_session.scalar(
        select(func.count()).select_from(Invoice).where(Invoice.store_id == store_id)
    )
    assert invoice_count == 1  # 重送不新建發票（不變量 2）


async def test_retry_rejects_non_failed(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _first_queue_id(svc, store_id)

    with pytest.raises(EInvoiceQueueNotRetryable):
        await svc.retry(store_id, queue_id)


async def test_retry_unknown_item_raises(db_session: AsyncSession) -> None:
    store_id, _sale_id = await _seed_sale(db_session)
    with pytest.raises(EInvoiceQueueItemNotFound):
        await EInvoiceService(db_session).retry(store_id, 999999)


# ── 折讓（F6 守衛）──


async def test_record_allowance_on_issued_enqueues_g0401(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    invoice = await _issue_and_accept(db_session, svc, store_id, sale_id, tmp_path)

    allowance = await svc.record_allowance(
        store_id, invoice_id=invoice.id, total=Decimal(210), tax_rate=TAX_RATE, return_id=1
    )

    assert allowance.net + allowance.tax == allowance.total
    items = await svc.list_queue(store_id, status=UploadStatus.PENDING)
    g = [i for i in items if i.action is EInvoiceAction.ALLOWANCE]
    assert len(g) == 1
    assert g[0].message_type is EInvoiceMessageType.G0401
    assert g[0].allowance_id == allowance.id


async def test_record_allowance_rejects_pending_invoice(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    invoice = await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    with pytest.raises(InvoiceNotIssued):
        await svc.record_allowance(
            store_id, invoice_id=invoice.id, total=Decimal(100), tax_rate=TAX_RATE
        )


async def test_record_allowance_rejects_duplicate_return(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    invoice = await _issue_and_accept(db_session, svc, store_id, sale_id, tmp_path)
    await svc.record_allowance(
        store_id, invoice_id=invoice.id, total=Decimal(100), tax_rate=TAX_RATE, return_id=7
    )
    with pytest.raises(DuplicateAllowanceForReturn):
        await svc.record_allowance(
            store_id, invoice_id=invoice.id, total=Decimal(50), tax_rate=TAX_RATE, return_id=7
        )


async def test_record_allowance_rejects_overage(db_session: AsyncSession, tmp_path: Path) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    invoice = await _issue_and_accept(db_session, svc, store_id, sale_id, tmp_path)  # total 1050
    await svc.record_allowance(
        store_id, invoice_id=invoice.id, total=Decimal(1000), tax_rate=TAX_RATE, return_id=1
    )
    with pytest.raises(AllowanceExceedsInvoice):
        await svc.record_allowance(
            store_id, invoice_id=invoice.id, total=Decimal(100), tax_rate=TAX_RATE, return_id=2
        )


# ── 作廢中止（F3）──


async def test_void_pending_invoice_marks_void_without_f0501(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    invoice = await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )

    voided = await svc.void_invoice_for_sale(store_id, sale_id)

    assert voided is not None
    assert voided.id == invoice.id
    assert voided.status is InvoiceStatus.VOID  # 從未上平台 → 直接正式作廢
    # 平台從未收過此發票 → 不送作廢訊息；原 F0401 待送列標 CANCELLED（明確終態、非殭屍 PENDING）。
    items = await svc.list_queue(store_id)
    assert [i.action for i in items] == [EInvoiceAction.ISSUE]
    assert items[0].status is UploadStatus.CANCELLED


async def test_void_pending_invoice_with_dropped_f0401_goes_void_pending(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    # H：F0401 已交付 Turnkey（dropped、待回執）時作廢 → 不可取消當沒收過，改 VOID_PENDING。
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _first_queue_id(svc, store_id)
    await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )

    voided = await svc.void_invoice_for_sale(store_id, sale_id)

    assert voided is not None
    assert voided.status is InvoiceStatus.VOID_PENDING
    item = (await svc.list_queue(store_id))[0]
    assert item.status is UploadStatus.PENDING  # F0401 未被取消（平台可能仍會開立）
    assert item.dropped_at is not None


async def test_f0401_success_while_void_requested_enqueues_f0501(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    invoice = await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    await _fill_issue_fields(db_session, invoice)
    queue_id = await _first_queue_id(svc, store_id)
    await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await svc.void_invoice_for_sale(store_id, sale_id)  # VOID_PENDING，F0401 仍在途

    await svc.record_result(store_id, queue_id, success=True)  # 平台其實開立了

    # → 續排 F0501 作廢；發票仍 VOID_PENDING（待 F0501 核可才正式 VOID）。
    assert (await svc.get_invoice(store_id, invoice.id)).status is InvoiceStatus.VOID_PENDING
    void_items = [i for i in await svc.list_queue(store_id) if i.action is EInvoiceAction.VOID]
    assert len(void_items) == 1
    assert void_items[0].message_type is EInvoiceMessageType.F0501


async def test_f0401_failure_while_void_requested_goes_void(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    invoice = await svc.create_pending_invoice(
        store_id, sale_id=sale_id, total=Decimal(1050), tax_rate=TAX_RATE
    )
    queue_id = await _first_queue_id(svc, store_id)
    await svc.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await svc.void_invoice_for_sale(store_id, sale_id)  # VOID_PENDING

    await svc.record_result(store_id, queue_id, success=False, message="E0001")  # 平台退回開立

    # 平台從未成功開立 → 收斂為正式 VOID（無需 F0501）。
    assert (await svc.get_invoice(store_id, invoice.id)).status is InvoiceStatus.VOID
    void_items = [i for i in await svc.list_queue(store_id) if i.action is EInvoiceAction.VOID]
    assert void_items == []


async def test_void_issued_invoice_f0501_flow_to_void(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    invoice = await _issue_and_accept(db_session, svc, store_id, sale_id, tmp_path)  # ISSUED

    voided = await svc.void_invoice_for_sale(store_id, sale_id)

    # 已核可發票作廢 → 先進 VOID_PENDING（尚未平台確認），並排 F0501（作廢）。
    assert voided is not None
    assert voided.status is InvoiceStatus.VOID_PENDING
    void_items = [i for i in await svc.list_queue(store_id) if i.action is EInvoiceAction.VOID]
    assert len(void_items) == 1
    assert void_items[0].message_type is EInvoiceMessageType.F0501
    assert void_items[0].invoice_id == invoice.id

    # F0501 拋檔（VOID_PENDING 發票的作廢訊息可拋）→ 平台核可 → 才轉正式 VOID（H3）。
    await svc.drop_pending(
        store_id, void_items[0].id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await svc.record_result(store_id, void_items[0].id, success=True)
    assert (await svc.get_invoice(store_id, invoice.id)).status is InvoiceStatus.VOID


async def test_void_invoice_is_idempotent(db_session: AsyncSession, tmp_path: Path) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    svc = EInvoiceService(db_session)
    await _issue_and_accept(db_session, svc, store_id, sale_id, tmp_path)

    await svc.void_invoice_for_sale(store_id, sale_id)
    await svc.void_invoice_for_sale(store_id, sale_id)  # 再次呼叫

    # 不重複排 F0501（只一筆作廢訊息）。
    void_items = [i for i in await svc.list_queue(store_id) if i.action is EInvoiceAction.VOID]
    assert len(void_items) == 1


async def test_void_invoice_for_sale_noop_when_no_invoice(db_session: AsyncSession) -> None:
    store_id, sale_id = await _seed_sale(db_session)
    assert await EInvoiceService(db_session).void_invoice_for_sale(store_id, sale_id) is None
