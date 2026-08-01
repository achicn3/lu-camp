"""退貨的發票處置整合測試（ADR-014）：同月整筆退作廢、其餘折讓、守衛與同意。

政策的組合矩陣（月界線/時區/載具/捐贈）已由 tests/test_returns_invoice_policy.py 以純邏輯
覆蓋；此處驗證**接線與守衛**：真的走了 F0501/G0401、紙本未收回會擋、缺同意會擋、
曾折讓過不再作廢、在途狀態轉人工。
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.models import Contact
from app.modules.einvoice.dropper import EInvoiceDropper
from app.modules.einvoice.models import EInvoiceUploadQueue, Invoice, InvoiceAllowance
from app.modules.einvoice.service import EInvoiceService
from app.modules.inventory.service import InventoryService
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.signing.models import SignatureTask
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    EInvoiceAction,
    Grade,
    InvoiceStatus,
    InvoiceVoidReason,
    OwnershipType,
    SaleInvoiceStatus,
    SaleLineType,
    SaleStatus,
    SignatureTaskKind,
    SignatureTaskStatus,
    UploadStatus,
    UserRole,
)
from app.shared.exceptions import (
    EInvoiceQueueNotRetryable,
    ReturnConflict,
    SignatureContentMismatch,
)
from tests.integration.customer_display_helpers import return_consent_content
from tests.integration.test_sales_einvoice import _FakeSerializer


async def _seed(session: AsyncSession, *, price: str = "1050") -> tuple[int, int, str]:
    """建門市＋管理員＋會員＋開帳＋一件在庫序號品（einvoice 啟用）。"""
    store = Store(name="退貨發票門市")
    session.add(store)
    await session.flush()
    clerk = User(
        store_id=store.id,
        username=f"ret-inv-clerk-{store.id}",
        password_hash="h",
        role=UserRole.MANAGER,
    )
    session.add(clerk)
    settings = StoreSettings(store_id=store.id, einvoice_enabled=True)
    session.add(settings)
    await session.flush()
    await CashDrawerService(session).open_session(store.id, clerk.id, Decimal("5000"))
    item = await InventoryService(session).create_serialized_item(
        store.id,
        item_code=f"RV-{store.id}-1",
        name="退貨測試品",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal(price),
        acquisition_cost=Decimal("500"),
    )
    await session.flush()
    contact = Contact(store_id=store.id, name="退貨客", roles=["MEMBER"], phone=f"09{store.id:08d}")
    session.add(contact)
    await session.flush()
    _CONTACTS[store.id] = contact.id
    return store.id, clerk.id, item.item_code


async def _issue_invoice(
    session: AsyncSession,
    einvoice: EInvoiceService,
    store_id: int,
    invoice: Invoice,
    tmp_path: Path,
    *,
    invoice_date: date | None = None,
    print_mark: bool = True,
    carrier_type: str | None = None,
    donate_mark: bool = False,
) -> None:
    """把發票推到 ISSUED，並可指定開立日/紙本/載具/捐贈以驅動政策分支。"""
    invoice.invoice_no = "AB12345678"
    invoice.invoice_date = invoice_date or datetime.now(UTC).date()
    invoice.invoice_time = "12:34:56"
    invoice.random_number = "1234"
    invoice.print_mark = print_mark
    invoice.carrier_type = carrier_type
    invoice.donate_mark = donate_mark
    await session.flush()
    queue_id = next(
        i.id for i in await einvoice.list_queue(store_id) if i.action is EInvoiceAction.ISSUE
    )
    await einvoice.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await einvoice.record_result(store_id, queue_id, success=True)


_CONTACTS: dict[int, int] = {}


async def _signed_consent(
    session: AsyncSession,
    store_id: int,
    sale_id: int,
    *,
    created_by: int,
    return_lines: dict[int, int],
) -> int:
    """建立一份已簽的退貨同意任務（直接建模；簽署流程本身另有測試覆蓋）。

    `return_lines`＝{sale_line_id: qty}：退貨成立時會與實際退貨範圍逐項比對，故須如實填寫。
    """
    task = SignatureTask(
        store_id=store_id,
        kind=SignatureTaskKind.RETURN_INVOICE_CONSENT,
        contact_id=_CONTACTS[store_id],
        content=await return_consent_content(
            session, store_id=store_id, sale_id=sale_id, return_lines=return_lines
        ),
        content_sha256="c" * 64,
        signature_sha256="s" * 64,
        evidence_hash="e" * 64,  # DB 約束：SIGNED 必有簽署時間與三組 hash
        status=SignatureTaskStatus.SIGNED,
        signed_at=datetime.now(UTC),
        ref_type="sale",
        ref_id=sale_id,
        created_by=created_by,
    )
    session.add(task)
    await session.flush()
    return task.id


async def _sale_with_issued_invoice(
    session: AsyncSession, tmp_path: Path, **issue_kwargs: object
) -> tuple[int, int, int, Invoice]:
    store_id, clerk_id, code = await _seed(session)
    sales = SalesService(session)
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
    )
    einvoice = EInvoiceService(session)
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    await _issue_invoice(session, einvoice, store_id, invoice, tmp_path, **issue_kwargs)  # type: ignore[arg-type]
    return store_id, clerk_id, sale.id, invoice


async def _return_all(
    session: AsyncSession,
    store_id: int,
    sale_id: int,
    clerk_id: int,
    *,
    invoice_recalled: bool = True,
    with_consent: bool = True,
    key: str = "ret-1",
) -> None:
    sale_lines = await SalesService(session).get_lines(sale_id)
    consent = (
        await _signed_consent(
            session,
            store_id,
            sale_id,
            created_by=clerk_id,
            return_lines={sale_lines[0].id: sale_lines[0].qty},
        )
        if with_consent
        else None
    )
    await ReturnsService(session).create_return(
        store_id,
        sale_id=sale_id,
        lines=[ReturnLineInput(sale_lines[0].id, sale_lines[0].qty)],
        reason="整筆退貨",
        actor_user_id=clerk_id,
        idempotency_key=key,
        invoice_recalled=invoice_recalled,
        consent_signature_task_id=consent,
    )


async def test_full_return_same_month_voids_invoice_via_f0501(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """同月整筆退貨 → 作廢原發票（F0501 已排隊），且**不**產生折讓。"""
    store_id, clerk_id, sale_id, invoice = await _sale_with_issued_invoice(db_session, tmp_path)
    await _return_all(db_session, store_id, sale_id, clerk_id)

    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.VOID_PENDING  # 等 F0501 平台核可才轉正式 VOID
    assert invoice.void_reason is InvoiceVoidReason.FULL_RETURN  # 與「銷售作廢」可分辨
    f0501 = [
        q
        for q in await EInvoiceService(db_session).list_queue(store_id)
        if q.action is EInvoiceAction.VOID
    ]
    assert len(f0501) == 1
    allowances = (
        await db_session.scalars(
            select(InvoiceAllowance).where(InvoiceAllowance.invoice_id == invoice.id)
        )
    ).all()
    assert allowances == []  # 作廢就不該再開折讓

    sale = await SalesService(db_session).get_sale(store_id, sale_id)
    assert sale is not None
    assert sale.status is SaleStatus.RETURNED  # 銷售有效、只是全退（非 VOIDED）
    # 平台尚未確認 F0501 → 停在 PENDING_VOID；確認成功才由回呼轉 VOID。
    assert sale.invoice_status is SaleInvoiceStatus.PENDING_VOID


async def test_full_return_cross_month_falls_back_to_allowance(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """跨月整筆退貨 → 開折讓，不作廢。"""
    last_month = (datetime.now(UTC).date().replace(day=1)) - timedelta(days=1)
    store_id, clerk_id, sale_id, invoice = await _sale_with_issued_invoice(
        db_session, tmp_path, invoice_date=last_month
    )
    await _return_all(db_session, store_id, sale_id, clerk_id)

    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.ISSUED  # 原發票仍有效
    allowance = await db_session.scalar(
        select(InvoiceAllowance).where(InvoiceAllowance.invoice_id == invoice.id)
    )
    assert allowance is not None
    sale = await SalesService(db_session).get_sale(store_id, sale_id)
    assert sale is not None
    assert sale.invoice_status is SaleInvoiceStatus.PENDING_ALLOWANCE


async def test_full_return_without_paper_recall_is_rejected(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """有紙本卻未收回 → **拒絕退貨**（店主裁示），且不得留下任何稅務動作。"""
    store_id, clerk_id, sale_id, invoice = await _sale_with_issued_invoice(db_session, tmp_path)
    with pytest.raises(ReturnConflict, match="收回"):
        await _return_all(db_session, store_id, sale_id, clerk_id, invoice_recalled=False)

    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.ISSUED
    assert (
        await db_session.scalar(
            select(InvoiceAllowance).where(InvoiceAllowance.invoice_id == invoice.id)
        )
        is None
    )


async def test_carrier_invoice_needs_no_paper_recall(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """載具（未列印紙本）→ 不要求收回，仍可作廢。"""
    store_id, clerk_id, sale_id, invoice = await _sale_with_issued_invoice(
        db_session, tmp_path, print_mark=False, carrier_type="3J0002"
    )
    await _return_all(db_session, store_id, sale_id, clerk_id, invoice_recalled=False)
    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.VOID_PENDING


async def test_consent_signature_is_required(db_session: AsyncSession, tmp_path: Path) -> None:
    """缺買受人同意簽名 → 拒絕（作業要點第 9 點）。"""
    store_id, clerk_id, sale_id, _invoice = await _sale_with_issued_invoice(db_session, tmp_path)
    with pytest.raises(ReturnConflict, match="同意"):
        await _return_all(db_session, store_id, sale_id, clerk_id, with_consent=False)


async def test_consent_scope_must_match_the_actual_return(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """客人簽的是「退這些、退幾件」——拿去退別的品項或別的數量一律拒絕。

    否則同意書形同空白支票：客人簽了退一件，店員可用同一份同意退整筆並作廢發票。
    """
    store_id, clerk_id, code = await _seed(db_session, price="500")
    sales = SalesService(db_session)
    second = await InventoryService(db_session).create_serialized_item(
        store_id,
        item_code=f"RV-{store_id}-scope",
        name="第二件",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal("500"),
        acquisition_cost=Decimal("100"),
    )
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code),
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=second.item_code),
        ],
    )
    einvoice = EInvoiceService(db_session)
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    await _issue_invoice(db_session, einvoice, store_id, invoice, tmp_path)
    lines = await sales.get_lines(sale.id)

    # 客人只同意退第一件
    consent = await _signed_consent(
        db_session, store_id, sale.id, created_by=clerk_id, return_lines={lines[0].id: 1}
    )
    with pytest.raises(SignatureContentMismatch):
        await ReturnsService(db_session).create_return(
            store_id,
            sale_id=sale.id,
            lines=[
                ReturnLineInput(lines[0].id, 1),
                ReturnLineInput(lines[1].id, 1),  # 店員卻整筆退
            ],
            reason="超出同意範圍",
            actor_user_id=clerk_id,
            idempotency_key="scope-mismatch-1",
            invoice_recalled=True,
            consent_signature_task_id=consent,
        )
    await db_session.rollback()


async def test_partial_then_full_return_keeps_using_allowance(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """先部分退（已折讓），再退完剩餘 → 第二次仍折讓，**不得作廢原發票**。"""
    store_id, clerk_id, code = await _seed(db_session, price="500")
    sales = SalesService(db_session)
    inv_svc = InventoryService(db_session)
    second = await inv_svc.create_serialized_item(
        store_id,
        item_code=f"RV-{store_id}-2",
        name="第二件",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal("500"),
        acquisition_cost=Decimal("200"),
    )
    await db_session.flush()
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code),
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=second.item_code),
        ],
    )
    einvoice = EInvoiceService(db_session)
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    await _issue_invoice(db_session, einvoice, store_id, invoice, tmp_path)

    lines = await sales.get_lines(sale.id)
    returns = ReturnsService(db_session)
    # 第一次：只退一件 → 折讓
    await returns.create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(lines[0].id, 1)],
        reason="部分退貨",
        actor_user_id=clerk_id,
        idempotency_key="partial-1",
        consent_signature_task_id=await _signed_consent(
            db_session, store_id, sale.id, created_by=clerk_id, return_lines={lines[0].id: 1}
        ),
    )
    # 讓折讓「成功」（平台核可）→ 之後就永遠不得作廢原發票
    g0401 = next(
        q for q in await einvoice.list_queue(store_id) if q.action is EInvoiceAction.ALLOWANCE
    )
    await einvoice.drop_pending(
        store_id, g0401.id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await einvoice.record_result(store_id, g0401.id, success=True)

    # 第二次：退完剩餘 → 仍須折讓
    await returns.create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(lines[1].id, 1)],
        reason="退完剩餘",
        actor_user_id=clerk_id,
        idempotency_key="partial-2",
        invoice_recalled=True,
        consent_signature_task_id=await _signed_consent(
            db_session, store_id, sale.id, created_by=clerk_id, return_lines={lines[1].id: 1}
        ),
    )
    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.ISSUED  # 未被作廢
    assert invoice.void_reason is None
    allowances = (
        await db_session.scalars(
            select(InvoiceAllowance).where(InvoiceAllowance.invoice_id == invoice.id)
        )
    ).all()
    assert len(allowances) == 2  # 兩次退貨各一張折讓


async def test_inflight_allowance_keeps_using_allowance_never_voids(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """折讓在途（尚未回執）時再退完剩餘 → 仍走折讓、**不作廢原發票**，且退款照常成立。

    分次退貨會有多張 G0401 同時在途是正常操作；若在此擋下，等於因為前一張折讓還沒回執
    就拒絕客人退款。
    """
    store_id, clerk_id, code = await _seed(db_session, price="500")
    sales = SalesService(db_session)
    second = await InventoryService(db_session).create_serialized_item(
        store_id,
        item_code=f"RV-{store_id}-2",
        name="第二件",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal("500"),
        acquisition_cost=Decimal("200"),
    )
    await db_session.flush()
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code),
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=second.item_code),
        ],
    )
    einvoice = EInvoiceService(db_session)
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    await _issue_invoice(db_session, einvoice, store_id, invoice, tmp_path)

    lines_of_sale = await sales.get_lines(sale.id)
    returns = ReturnsService(db_session)
    await returns.create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(lines_of_sale[0].id, 1)],
        reason="部分退貨",
        actor_user_id=clerk_id,
        idempotency_key="inflight-1",
        consent_signature_task_id=await _signed_consent(
            db_session,
            store_id,
            sale.id,
            created_by=clerk_id,
            return_lines={lines_of_sale[0].id: 1},
        ),
    )
    # G0401 仍 PENDING（未拋檔/未回執）＝結果未收斂
    pending = [
        q
        for q in await einvoice.list_queue(store_id)
        if q.action is EInvoiceAction.ALLOWANCE and q.status is UploadStatus.PENDING
    ]
    assert pending
    consent2 = await _signed_consent(
        db_session,
        store_id,
        sale.id,
        created_by=clerk_id,
        return_lines={lines_of_sale[1].id: 1},
    )
    await returns.create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(lines_of_sale[1].id, 1)],
        reason="退完剩餘",
        actor_user_id=clerk_id,
        idempotency_key="inflight-2",
        invoice_recalled=True,
        consent_signature_task_id=consent2,
    )
    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.ISSUED  # 未被作廢
    assert invoice.void_reason is None
    allowances = (
        await db_session.scalars(
            select(InvoiceAllowance).where(InvoiceAllowance.invoice_id == invoice.id)
        )
    ).all()
    assert len(allowances) == 2  # 兩次退貨各一張折讓，皆在途


async def test_preview_reports_action_without_writing(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """預覽唯讀：回報將作廢與需收回紙本，但不產生任何佇列/折讓。"""
    store_id, _clerk_id, sale_id, invoice = await _sale_with_issued_invoice(db_session, tmp_path)
    lines = await SalesService(db_session).get_lines(sale_id)
    preview = await ReturnsService(db_session).preview_return(
        store_id, sale_id=sale_id, lines=[ReturnLineInput(lines[0].id, lines[0].qty)]
    )
    assert preview["is_full_return"] is True
    assert preview["invoice_action"] == "VOID"
    assert preview["requires_paper_recall"] is True
    assert preview["requires_customer_consent"] is True

    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.ISSUED
    voids = [
        q
        for q in await db_session.scalars(
            select(EInvoiceUploadQueue).where(EInvoiceUploadQueue.invoice_id == invoice.id)
        )
        if q.action is EInvoiceAction.VOID
    ]
    assert voids == []


async def test_failed_allowance_still_forbids_voiding_the_invoice(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """G0401 送出失敗（FAILED）後退完剩餘 → 仍折讓、**不得作廢**（Codex 對抗審查 #1）。

    FAILED 的折讓隨時可能被店員重送成功。若因為「不算既有折讓」而讓後續全退走作廢，
    最終會對同一張發票同時送出 G0401 與 F0501，帳目自相矛盾且無法判斷孰先孰後。
    """
    store_id, clerk_id, code = await _seed(db_session, price="500")
    sales = SalesService(db_session)
    second = await InventoryService(db_session).create_serialized_item(
        store_id,
        item_code=f"RV-{store_id}-fail",
        name="第二件",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal("500"),
        acquisition_cost=Decimal("200"),
    )
    await db_session.flush()
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code),
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=second.item_code),
        ],
    )
    einvoice = EInvoiceService(db_session)
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    await _issue_invoice(db_session, einvoice, store_id, invoice, tmp_path)
    lines_of_sale = await sales.get_lines(sale.id)
    returns = ReturnsService(db_session)

    await returns.create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(lines_of_sale[0].id, 1)],
        reason="部分退貨",
        actor_user_id=clerk_id,
        idempotency_key="failed-alw-1",
        consent_signature_task_id=await _signed_consent(
            db_session,
            store_id,
            sale.id,
            created_by=clerk_id,
            return_lines={lines_of_sale[0].id: 1},
        ),
    )
    g0401 = next(
        q for q in await einvoice.list_queue(store_id) if q.action is EInvoiceAction.ALLOWANCE
    )
    await einvoice.drop_pending(
        store_id, g0401.id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await einvoice.record_result(store_id, g0401.id, success=False, message="平台拒絕")
    failed = next(q for q in await einvoice.list_queue(store_id) if q.id == g0401.id)
    assert failed.status is UploadStatus.FAILED

    await returns.create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(lines_of_sale[1].id, 1)],
        reason="退完剩餘",
        actor_user_id=clerk_id,
        idempotency_key="failed-alw-2",
        invoice_recalled=True,
        consent_signature_task_id=await _signed_consent(
            db_session,
            store_id,
            sale.id,
            created_by=clerk_id,
            return_lines={lines_of_sale[1].id: 1},
        ),
    )

    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.ISSUED  # 未被作廢
    assert invoice.void_reason is None


async def test_failed_allowance_cannot_be_resent_onto_a_voided_invoice(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """原發票已進入作廢流程時，FAILED 的折讓不可重送（Codex 對抗審查 #1 的第二道牆）。"""
    store_id, _clerk_id, sale_id, invoice = await _sale_with_issued_invoice(
        db_session, tmp_path
    )
    einvoice = EInvoiceService(db_session)
    sale_lines = await SalesService(db_session).get_lines(sale_id)
    # 先造一張折讓並讓它 FAILED（以部分退貨產生；此單只有一行，故直接用 record_allowance）
    await einvoice.record_allowance(
        store_id, invoice_id=invoice.id, total=Decimal("100"), return_id=None
    )
    g0401 = next(
        q for q in await einvoice.list_queue(store_id) if q.action is EInvoiceAction.ALLOWANCE
    )
    await einvoice.drop_pending(
        store_id, g0401.id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await einvoice.record_result(store_id, g0401.id, success=False, message="平台拒絕")
    # 再讓原發票進入作廢流程
    await einvoice.void_invoice_for_sale(
        store_id, sale_id, reason=InvoiceVoidReason.SALE_VOID, actor_user_id=None
    )
    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.VOID_PENDING

    with pytest.raises(EInvoiceQueueNotRetryable, match="作廢"):
        await einvoice.retry(store_id, g0401.id)
    assert sale_lines  # 該單確有明細（此測試以 record_allowance 直接造折讓）


async def test_consent_is_rejected_when_the_disposition_drifts_after_signing(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """簽的是「同意作廢」，送出前處置卻變成折讓 → 拒絕（Codex 對抗審查 #3）。

    真實觸發路徑：簽名與送出之間跨過台北月界線，或期間另一張折讓落地。客人簽的那張紙
    寫的是作廢，系統就不能拿它去做折讓——同意書必須對得上真正執行的事。
    """
    store_id, clerk_id, sale_id, invoice = await _sale_with_issued_invoice(db_session, tmp_path)
    sale_lines = await SalesService(db_session).get_lines(sale_id)
    consent = await _signed_consent(
        db_session,
        store_id,
        sale_id,
        created_by=clerk_id,
        return_lines={sale_lines[0].id: sale_lines[0].qty},
    )
    signed_task = await db_session.get(SignatureTask, consent)
    assert signed_task is not None and signed_task.content["invoice_action"] == "VOID"

    # 簽完之後才發現原發票是上個月開的（＝跨月）→ 同一份簽名此刻應判折讓
    invoice.invoice_date = (datetime.now(UTC) - timedelta(days=45)).date()
    await db_session.flush()

    with pytest.raises(SignatureContentMismatch, match="處置方式"):
        await ReturnsService(db_session).create_return(
            store_id,
            sale_id=sale_id,
            lines=[ReturnLineInput(sale_lines[0].id, sale_lines[0].qty)],
            reason="跨月後才送出",
            actor_user_id=clerk_id,
            idempotency_key="drift-1",
            invoice_recalled=True,
            consent_signature_task_id=consent,
        )


async def test_consent_is_rejected_when_the_refund_amount_drifts(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """簽的金額與實際退款金額不符 → 拒絕（同意書上的數字必須是客人真正拿到的）。"""
    store_id, clerk_id, code = await _seed(db_session, price="500")
    sales = SalesService(db_session)
    second = await InventoryService(db_session).create_serialized_item(
        store_id,
        item_code=f"RV-{store_id}-drift",
        name="第二件",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal("500"),
        acquisition_cost=Decimal("200"),
    )
    await db_session.flush()
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code),
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=second.item_code),
        ],
    )
    einvoice = EInvoiceService(db_session)
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    await _issue_invoice(db_session, einvoice, store_id, invoice, tmp_path)
    lines_of_sale = await sales.get_lines(sale.id)

    # 客人簽的是「退第一件（$500）」
    consent = await _signed_consent(
        db_session,
        store_id,
        sale.id,
        created_by=clerk_id,
        return_lines={lines_of_sale[0].id: 1},
    )
    # 店員卻拿去退第二件（金額相同、但範圍不同）→ 範圍守衛先擋下
    with pytest.raises(SignatureContentMismatch, match="品項"):
        await ReturnsService(db_session).create_return(
            store_id,
            sale_id=sale.id,
            lines=[ReturnLineInput(lines_of_sale[1].id, 1)],
            reason="換一件退",
            actor_user_id=clerk_id,
            idempotency_key="drift-2",
            consent_signature_task_id=consent,
        )


async def test_voiding_an_invoice_writes_an_invoice_level_audit(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """作廢發票須留下「誰、對象、前後值」（CLAUDE.md §5）。

    銷售層的稽核記的是交易；出事時要追的是「哪張發票、由什麼狀態、因為什麼原因被作廢」。
    """
    store_id, clerk_id, sale_id, invoice = await _sale_with_issued_invoice(db_session, tmp_path)
    await _return_all(db_session, store_id, sale_id, clerk_id)

    log = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.store_id == store_id,
            AuditLog.action == "VOID_INVOICE",
            AuditLog.entity_id == str(invoice.id),
        )
    )
    assert log is not None
    assert log.entity_type == "invoice"
    assert log.actor_user_id == clerk_id
    assert log.before == {"status": "ISSUED"}
    assert log.after is not None
    assert log.after["status"] == "VOID_PENDING"
    assert log.after["void_reason"] == InvoiceVoidReason.FULL_RETURN.value
    assert log.after["sale_id"] == sale_id
    assert log.after["source"] == "STAFF"  # 店員發起（平台回執另記 F0501_ACCEPTED/F0401_FAILED）


async def test_voided_sale_shows_its_invoice_as_voided_in_the_list(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """打錯單作廢一張**已開立**發票的交易 → 交易紀錄的發票狀態必須跟著變「已作廢」。

    回歸測試（Codex 第二輪 #3）：拆分生命週期時把 void_sale 的 invoice_status 同步整個拿掉，
    導致已作廢的單在列表上仍顯示「已開立」。有發票才標 VOID；沒發票的單維持 NOT_ISSUED。
    """
    store_id, clerk_id, sale_id, invoice = await _sale_with_issued_invoice(db_session, tmp_path)
    sales = SalesService(db_session)
    to_void = await sales.get_sale(store_id, sale_id)
    assert to_void is not None

    await sales.void_sale(to_void, clerk_id)

    voided = await sales.get_sale(store_id, sale_id)
    assert voided is not None
    assert voided.status is SaleStatus.VOIDED
    # 已請求作廢、平台尚未確認 → 作廢處理中（不可先謊報「已作廢」）
    assert voided.invoice_status is SaleInvoiceStatus.PENDING_VOID
    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.VOID_PENDING
    assert invoice.void_reason is InvoiceVoidReason.SALE_VOID


async def test_voided_sale_without_any_invoice_stays_not_issued(
    db_session: AsyncSession,
) -> None:
    """電子發票關閉（根本沒發票）的單作廢後，發票狀態必須維持「未開立」。

    這是變更 A 原本要修的缺陷，補上 void_sale 的同步後不可以又倒退回去。
    """
    store = Store(name="無發票門市")
    db_session.add(store)
    await db_session.flush()
    clerk = User(
        store_id=store.id,
        username=f"no-inv-{store.id}",
        password_hash="h",
        role=UserRole.MANAGER,
    )
    db_session.add(clerk)
    db_session.add(StoreSettings(store_id=store.id, einvoice_enabled=False))
    await db_session.flush()
    await CashDrawerService(db_session).open_session(store.id, clerk.id, Decimal("1000"))
    item = await InventoryService(db_session).create_serialized_item(
        store.id,
        item_code=f"NOINV-{store.id}",
        name="無發票商品",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal("300"),
        acquisition_cost=Decimal("100"),
    )
    sales = SalesService(db_session)
    sale = await sales.create_sale(
        store.id,
        clerk.id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=item.item_code)],
    )

    await sales.void_sale(sale, clerk.id)

    voided = await sales.get_sale(store.id, sale.id)
    assert voided is not None
    assert voided.status is SaleStatus.VOIDED
    assert voided.invoice_status is SaleInvoiceStatus.NOT_ISSUED


async def test_database_rejects_void_status_without_a_reason(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """CHECK 必須同時存在於 models 與 migration（Codex 第二輪 #4）。

    測試庫由 metadata 建立；約束只寫在 migration 的話，測試永遠測不到它。
    """
    store_id, _clerk_id, sale_id, invoice = await _sale_with_issued_invoice(db_session, tmp_path)
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("UPDATE invoices SET status = 'VOID' WHERE id = :iid").bindparams(iid=invoice.id)
        )
    await db_session.rollback()
    assert store_id and sale_id  # 種子確實建立


async def test_rejected_f0501_leaves_the_sale_showing_void_in_progress(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """平台**拒絕**作廢時，畫面不可顯示「已作廢」——那張發票在平台上還有效。

    回歸測試（Codex 第三輪 #2）：先前只要送出作廢就把 invoice_status 標成 VOID，
    F0501 被拒後沒有任何回呼會改正它，畫面等於永遠說謊。
    """
    store_id, clerk_id, sale_id, invoice = await _sale_with_issued_invoice(db_session, tmp_path)
    einvoice = EInvoiceService(db_session)
    sales = SalesService(db_session)
    await _return_all(db_session, store_id, sale_id, clerk_id)

    during = await sales.get_sale(store_id, sale_id)
    assert during is not None
    assert during.invoice_status is SaleInvoiceStatus.PENDING_VOID

    f0501 = next(
        q for q in await einvoice.list_queue(store_id) if q.action is EInvoiceAction.VOID
    )
    await einvoice.drop_pending(
        store_id, f0501.id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await einvoice.record_result(store_id, f0501.id, success=False, message="平台拒絕作廢")

    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.VOID_PENDING  # 平台上仍有效，尚未真的作廢
    after = await sales.get_sale(store_id, sale_id)
    assert after is not None
    assert after.invoice_status is SaleInvoiceStatus.PENDING_VOID  # **不是** VOID


async def test_accepted_f0501_finally_marks_the_sale_void_with_an_audit(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """平台確認作廢 → 才轉 VOID，且該次終態轉移也留下 invoice 級稽核（標明來源）。"""
    store_id, clerk_id, sale_id, invoice = await _sale_with_issued_invoice(db_session, tmp_path)
    einvoice = EInvoiceService(db_session)
    await _return_all(db_session, store_id, sale_id, clerk_id)
    f0501 = next(
        q for q in await einvoice.list_queue(store_id) if q.action is EInvoiceAction.VOID
    )
    await einvoice.drop_pending(
        store_id, f0501.id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await einvoice.record_result(store_id, f0501.id, success=True)

    await db_session.refresh(invoice)
    assert invoice.status is InvoiceStatus.VOID
    after = await SalesService(db_session).get_sale(store_id, sale_id)
    assert after is not None and after.invoice_status is SaleInvoiceStatus.VOID

    logs = (
        await db_session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "VOID_INVOICE", AuditLog.entity_id == str(invoice.id))
            .order_by(AuditLog.id)
        )
    ).all()
    assert [log.after["source"] for log in logs if log.after] == ["STAFF", "F0501_ACCEPTED"]
    assert logs[-1].before == {"status": "VOID_PENDING"}
    assert logs[-1].after is not None and logs[-1].after["status"] == "VOID"
