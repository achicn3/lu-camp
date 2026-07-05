"""sales/returns → einvoice 整合：結帳觸發開立、平台核可回同步、退貨折讓（§6/§7.5）。

開啟時：結帳於同一原子交易建 PENDING 發票 + F0401 佇列，sale.invoice_status=PENDING_ISSUE
（非「已開立」）；平台 ProcessResult 核可後同步轉 ISSUED（H2）。關閉時維持 NOT_ISSUED、不建發票。
已開票銷售退貨 → 產 G0401 折讓、sale→ALLOWANCE（H1）。冪等重送不重複開立。
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.cashdrawer.service import CashDrawerService
from app.modules.einvoice.dropper import EInvoiceDropper
from app.modules.einvoice.models import EInvoiceUploadQueue, Invoice, InvoiceAllowance
from app.modules.einvoice.service import EInvoiceService
from app.modules.inventory.service import InventoryService
from app.modules.returns.service import ReturnLineInput, ReturnsService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.service import SalesService
from app.modules.settings.models import StoreSettings
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import (
    EInvoiceAction,
    EInvoiceMessageType,
    Grade,
    InvoiceStatus,
    OwnershipType,
    SaleInvoiceStatus,
    SaleLineType,
    UploadStatus,
    UserRole,
)


class _FakeSerializer:
    def serialize_invoice(self, invoice: Invoice, message_type: EInvoiceMessageType) -> bytes:
        return b"<Invoice/>"

    def serialize_allowance(self, allowance: object, message_type: EInvoiceMessageType) -> bytes:
        return b"<Allowance/>"


async def _accept_invoice(
    session: AsyncSession,
    einvoice: EInvoiceService,
    store_id: int,
    invoice: Invoice,
    tmp_path: Path,
) -> None:
    """模擬配號/序列化 + 拋檔 + 平台 ProcessResult 成功，把發票推到 ISSUED。"""
    invoice.invoice_no = "AB12345678"
    invoice.invoice_date = date(2026, 7, 1)
    invoice.invoice_time = "12:34:56"
    invoice.random_number = "1234"
    await session.flush()
    queue_id = next(
        i.id for i in await einvoice.list_queue(store_id) if i.action is EInvoiceAction.ISSUE
    )
    await einvoice.drop_pending(
        store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await einvoice.record_result(store_id, queue_id, success=True)


async def _seed(session: AsyncSession, *, einvoice_enabled: bool) -> tuple[int, int, str]:
    """建 store + clerk + 開帳 + 自有序號品（listed_price=1050）；回 (store_id, clerk_id, code)。"""
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    clerk = User(store_id=store.id, username="clk", password_hash="h", role=UserRole.CLERK)
    session.add(clerk)
    await session.flush()
    if einvoice_enabled:
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


async def _seed_second_item(session: AsyncSession, store_id: int) -> str:
    """加一件自有序號品（listed_price=1050）供部分退貨情境；回 item_code。"""
    item = await InventoryService(session).create_serialized_item(
        store_id,
        item_code="SN-2",
        name="鏡頭",
        grade=Grade.A,
        ownership_type=OwnershipType.OWNED,
        listed_price=Decimal(1050),
        acquisition_cost=Decimal(500),
    )
    return item.item_code


async def _return_lines(
    session: AsyncSession,
    store_id: int,
    clerk_id: int,
    sale_id: int,
    line_ids: list[int],
    *,
    idem: str,
) -> None:
    """退掉指定明細行（各 qty=1）。"""
    await ReturnsService(session).create_return(
        store_id,
        sale_id=sale_id,
        lines=[ReturnLineInput(sale_line_id=lid, qty=1) for lid in line_ids],
        reason="測試退貨",
        actor_user_id=clerk_id,
        idempotency_key=idem,
    )


async def test_checkout_queues_pending_invoice_when_enabled(db_session: AsyncSession) -> None:
    store_id, clerk_id, code = await _seed(db_session, einvoice_enabled=True)

    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
    )

    # 尚未平台核可 → PENDING_ISSUE（非「已開立」）
    assert sale.invoice_status is SaleInvoiceStatus.PENDING_ISSUE
    invoice = await db_session.scalar(select(Invoice).where(Invoice.sale_id == sale.id))
    assert invoice is not None
    assert invoice.status is InvoiceStatus.PENDING
    assert invoice.invoice_no is None
    assert invoice.total == sale.total == Decimal(1050)
    assert invoice.net == sale.subtotal  # 發票與銷售同口徑推稅
    assert invoice.tax == sale.tax
    item = await db_session.scalar(
        select(EInvoiceUploadQueue).where(EInvoiceUploadQueue.store_id == store_id)
    )
    assert item is not None
    assert item.status is UploadStatus.PENDING
    assert item.message_type is EInvoiceMessageType.F0401
    assert item.invoice_id == invoice.id


async def test_void_sale_voids_pending_invoice(db_session: AsyncSession) -> None:
    store_id, clerk_id, code = await _seed(db_session, einvoice_enabled=True)
    svc = SalesService(db_session)
    sale = await svc.create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
    )

    await svc.void_sale(sale, clerk_id)

    assert sale.invoice_status is SaleInvoiceStatus.VOID
    invoice = await db_session.scalar(select(Invoice).where(Invoice.sale_id == sale.id))
    assert invoice is not None
    assert invoice.status is InvoiceStatus.VOID  # 待送發票被中止，drop_pending 將拒絕


async def test_checkout_no_invoice_when_disabled(db_session: AsyncSession) -> None:
    store_id, clerk_id, code = await _seed(db_session, einvoice_enabled=False)

    sale = await SalesService(db_session).create_sale(
        store_id,
        clerk_id,
        lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)],
    )

    assert sale.invoice_status is SaleInvoiceStatus.NOT_ISSUED
    count = await db_session.scalar(
        select(func.count()).select_from(Invoice).where(Invoice.sale_id == sale.id)
    )
    assert count == 0


async def test_idempotent_replay_does_not_double_issue(db_session: AsyncSession) -> None:
    store_id, clerk_id, code = await _seed(db_session, einvoice_enabled=True)
    svc = SalesService(db_session)
    line = SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)

    first = await svc.create_sale(store_id, clerk_id, lines=[line], idempotency_key="k1")
    again = await svc.create_sale(store_id, clerk_id, lines=[line], idempotency_key="k1")

    assert first.id == again.id
    count = await db_session.scalar(
        select(func.count()).select_from(Invoice).where(Invoice.store_id == store_id)
    )
    assert count == 1  # 冪等重送不重複開立


async def test_sale_status_syncs_to_issued_on_platform_accept(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """H2：平台 ProcessResult 核可後，sale.invoice_status 由 PENDING_ISSUE 同步轉 ISSUED。"""
    store_id, clerk_id, code = await _seed(db_session, einvoice_enabled=True)
    sales = SalesService(db_session)
    sale = await sales.create_sale(
        store_id, clerk_id, lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)]
    )
    assert sale.invoice_status is SaleInvoiceStatus.PENDING_ISSUE

    einvoice = EInvoiceService(db_session)
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    await _accept_invoice(db_session, einvoice, store_id, invoice, tmp_path)

    synced = await sales.get_sale(store_id, sale.id)
    assert synced is not None
    assert synced.invoice_status is SaleInvoiceStatus.ISSUED
    assert (await einvoice.get_invoice(store_id, invoice.id)).status is InvoiceStatus.ISSUED


async def test_return_of_issued_sale_creates_g0401_allowance(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """H1：原銷售已正式開票後退貨 → 產生 G0401 折讓、sale.invoice_status=ALLOWANCE（§7.5）。"""
    store_id, clerk_id, code = await _seed(db_session, einvoice_enabled=True)
    sales = SalesService(db_session)
    sale = await sales.create_sale(
        store_id, clerk_id, lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)]
    )
    einvoice = EInvoiceService(db_session)
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    await _accept_invoice(db_session, einvoice, store_id, invoice, tmp_path)  # 發票 → ISSUED

    sale_lines = await sales.get_lines(sale.id)
    customer_return = await ReturnsService(db_session).create_return(
        store_id,
        sale_id=sale.id,
        lines=[ReturnLineInput(sale_line_id=sale_lines[0].id, qty=1)],
        reason="不合適退貨",
        actor_user_id=clerk_id,
        idempotency_key="ret-1",
    )

    # 退貨當下：折讓已建 + G0401 排隊，但 sale 先進 PENDING_ALLOWANCE（等平台成功才轉正式）。
    synced = await sales.get_sale(store_id, sale.id)
    assert synced is not None
    assert synced.invoice_status is SaleInvoiceStatus.PENDING_ALLOWANCE
    allowance = await db_session.scalar(
        select(InvoiceAllowance).where(InvoiceAllowance.return_id == customer_return.id)
    )
    assert allowance is not None
    assert allowance.invoice_id == invoice.id
    assert allowance.total == Decimal(1050)  # 全額退 → 折讓全額
    g0401 = [i for i in await einvoice.list_queue(store_id) if i.action is EInvoiceAction.ALLOWANCE]
    assert len(g0401) == 1
    assert g0401[0].message_type is EInvoiceMessageType.G0401

    # G0401 拋檔 + 平台核可 → sale 才轉正式 ALLOWANCE。
    await einvoice.drop_pending(
        store_id, g0401[0].id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await einvoice.record_result(store_id, g0401[0].id, success=True)
    synced_after = await sales.get_sale(store_id, sale.id)
    assert synced_after is not None
    assert synced_after.invoice_status is SaleInvoiceStatus.ALLOWANCE


# ── 發票核可前退貨的收斂（B）──


async def test_full_return_before_drop_cancels_invoice(db_session: AsyncSession) -> None:
    """全退且 F0401 未拋檔：發票 VOID、佇列 CANCELLED、sale 收斂 NOT_ISSUED（無有效發票）。"""
    store_id, clerk_id, code = await _seed(db_session, einvoice_enabled=True)
    sales = SalesService(db_session)
    sale = await sales.create_sale(
        store_id, clerk_id, lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)]
    )
    sale_lines = await sales.get_lines(sale.id)

    await _return_lines(db_session, store_id, clerk_id, sale.id, [sale_lines[0].id], idem="r1")

    einvoice = EInvoiceService(db_session)
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    assert invoice.status is InvoiceStatus.VOID
    items = await einvoice.list_queue(store_id)
    assert [i.status for i in items] == [UploadStatus.CANCELLED]
    synced = await sales.get_sale(store_id, sale.id)
    assert synced is not None
    assert synced.invoice_status is SaleInvoiceStatus.NOT_ISSUED


async def test_full_return_with_inflight_f0401_converges_via_f0501(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """全退但 F0401 已拋檔（在途）：發票 VOID_PENDING、F0401 保留；平台仍核可 → 續 F0501
    作廢 → F0501 核可 → 發票 VOID、sale 收斂 NOT_ISSUED。"""
    store_id, clerk_id, code = await _seed(db_session, einvoice_enabled=True)
    sales = SalesService(db_session)
    einvoice = EInvoiceService(db_session)
    sale = await sales.create_sale(
        store_id, clerk_id, lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)]
    )
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    invoice.invoice_no = "AB12345678"
    invoice.invoice_date = date(2026, 7, 1)
    invoice.invoice_time = "12:34:56"
    invoice.random_number = "1234"
    await db_session.flush()
    f0401_id = (await einvoice.list_queue(store_id))[0].id
    await einvoice.drop_pending(
        store_id, f0401_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )

    sale_lines = await sales.get_lines(sale.id)
    await _return_lines(db_session, store_id, clerk_id, sale.id, [sale_lines[0].id], idem="r1")

    refreshed = await einvoice.get_invoice(store_id, invoice.id)
    assert refreshed.status is InvoiceStatus.VOID_PENDING  # 在途，不可當平台沒收過

    # F0401 平台仍核可 → 自動續排 F0501 作廢。
    await einvoice.record_result(store_id, f0401_id, success=True)
    void_items = [i for i in await einvoice.list_queue(store_id) if i.action is EInvoiceAction.VOID]
    assert len(void_items) == 1
    # F0501 核可 → 正式 VOID、sale 收斂 NOT_ISSUED。
    await einvoice.drop_pending(
        store_id, void_items[0].id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await einvoice.record_result(store_id, void_items[0].id, success=True)
    assert (await einvoice.get_invoice(store_id, invoice.id)).status is InvoiceStatus.VOID
    synced = await sales.get_sale(store_id, sale.id)
    assert synced is not None
    assert synced.invoice_status is SaleInvoiceStatus.NOT_ISSUED


async def test_full_return_inflight_then_f0401_failure_goes_void(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """全退＋F0401 在途，之後平台退回開立 → 發票 VOID、sale 收斂 NOT_ISSUED（無需 F0501）。"""
    store_id, clerk_id, code = await _seed(db_session, einvoice_enabled=True)
    sales = SalesService(db_session)
    einvoice = EInvoiceService(db_session)
    sale = await sales.create_sale(
        store_id, clerk_id, lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code)]
    )
    f0401_id = (await einvoice.list_queue(store_id))[0].id
    await einvoice.drop_pending(
        store_id, f0401_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    sale_lines = await sales.get_lines(sale.id)
    await _return_lines(db_session, store_id, clerk_id, sale.id, [sale_lines[0].id], idem="r1")

    await einvoice.record_result(store_id, f0401_id, success=False, message="E0001")

    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    assert invoice.status is InvoiceStatus.VOID
    synced = await sales.get_sale(store_id, sale.id)
    assert synced is not None
    assert synced.invoice_status is SaleInvoiceStatus.NOT_ISSUED


async def test_partial_return_before_accept_backfills_allowance(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """部分退且發票未核可：F0401 核可（發票成立）時自動補開 G0401、sale→PENDING_ALLOWANCE。"""
    store_id, clerk_id, code1 = await _seed(db_session, einvoice_enabled=True)
    code2 = await _seed_second_item(db_session, store_id)
    sales = SalesService(db_session)
    einvoice = EInvoiceService(db_session)
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code1),
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code2),
        ],
    )
    sale_lines = await sales.get_lines(sale.id)
    # 部分退（兩件退一件）：發票仍 PENDING、不動。
    await _return_lines(db_session, store_id, clerk_id, sale.id, [sale_lines[0].id], idem="r1")
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    assert invoice.status is InvoiceStatus.PENDING

    # F0401 核可 → 發票 ISSUED＋自動補開折讓（金額=該退貨退款額）→ sale PENDING_ALLOWANCE。
    invoice.invoice_no = "AB12345678"
    invoice.invoice_date = date(2026, 7, 1)
    invoice.invoice_time = "12:34:56"
    invoice.random_number = "1234"
    await db_session.flush()
    f0401_id = next(
        i.id for i in await einvoice.list_queue(store_id) if i.action is EInvoiceAction.ISSUE
    )
    await einvoice.drop_pending(
        store_id, f0401_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await einvoice.record_result(store_id, f0401_id, success=True)

    assert (await einvoice.get_invoice(store_id, invoice.id)).status is InvoiceStatus.ISSUED
    allowance = await db_session.scalar(
        select(InvoiceAllowance).where(InvoiceAllowance.invoice_id == invoice.id)
    )
    assert allowance is not None
    assert allowance.total == Decimal(1050)  # 退一件 → 折讓一件的實付額
    synced = await sales.get_sale(store_id, sale.id)
    assert synced is not None
    assert synced.invoice_status is SaleInvoiceStatus.PENDING_ALLOWANCE
    g0401 = [i for i in await einvoice.list_queue(store_id) if i.action is EInvoiceAction.ALLOWANCE]
    assert len(g0401) == 1


async def test_failed_allowance_blocks_sale_allowance_until_resolved(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Codex 第八輪回歸：一張折讓 FAILED、另一張成功 → sale 不得標 ALLOWANCE；
    失敗折讓 retry→重拋→核可後才轉正式 ALLOWANCE。"""
    store_id, clerk_id, code1 = await _seed(db_session, einvoice_enabled=True)
    code2 = await _seed_second_item(db_session, store_id)
    sales = SalesService(db_session)
    einvoice = EInvoiceService(db_session)
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code1),
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code2),
        ],
    )
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    await _accept_invoice(db_session, einvoice, store_id, invoice, tmp_path)  # ISSUED

    sale_lines = await sales.get_lines(sale.id)
    await _return_lines(db_session, store_id, clerk_id, sale.id, [sale_lines[0].id], idem="r1")
    await _return_lines(db_session, store_id, clerk_id, sale.id, [sale_lines[1].id], idem="r2")
    g0401 = [i for i in await einvoice.list_queue(store_id) if i.action is EInvoiceAction.ALLOWANCE]
    assert len(g0401) == 2
    dropper = EInvoiceDropper(tmp_path)

    # 第一張失敗、第二張成功 → sale 仍不得標 ALLOWANCE（失敗折讓未解決）。
    await einvoice.drop_pending(
        store_id, g0401[0].id, serializer=_FakeSerializer(), dropper=dropper
    )
    await einvoice.record_result(store_id, g0401[0].id, success=False, message="E0001")
    await einvoice.drop_pending(
        store_id, g0401[1].id, serializer=_FakeSerializer(), dropper=dropper
    )
    await einvoice.record_result(store_id, g0401[1].id, success=True)
    mid = await sales.get_sale(store_id, sale.id)
    assert mid is not None
    assert mid.invoice_status is SaleInvoiceStatus.PENDING_ALLOWANCE  # FAILED 也算未解決

    # 失敗折讓 retry → 重拋（新世代）→ 核可 → 全數成功終結 → 轉正式 ALLOWANCE。
    await einvoice.retry(store_id, g0401[0].id)
    await einvoice.drop_pending(
        store_id, g0401[0].id, serializer=_FakeSerializer(), dropper=dropper
    )
    await einvoice.record_result(store_id, g0401[0].id, success=True, delivery_attempt=1)
    done = await sales.get_sale(store_id, sale.id)
    assert done is not None
    assert done.invoice_status is SaleInvoiceStatus.ALLOWANCE


async def test_multiple_inflight_allowances_transition_only_when_all_accepted(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """兩張折讓在途：第一張核可 sale 仍 PENDING_ALLOWANCE，全部核可才轉正式 ALLOWANCE（F）。"""
    store_id, clerk_id, code1 = await _seed(db_session, einvoice_enabled=True)
    code2 = await _seed_second_item(db_session, store_id)
    sales = SalesService(db_session)
    einvoice = EInvoiceService(db_session)
    sale = await sales.create_sale(
        store_id,
        clerk_id,
        lines=[
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code1),
            SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=code2),
        ],
    )
    invoice = await einvoice.get_invoice_for_sale(store_id, sale.id)
    assert invoice is not None
    await _accept_invoice(db_session, einvoice, store_id, invoice, tmp_path)  # 發票 ISSUED

    sale_lines = await sales.get_lines(sale.id)
    await _return_lines(db_session, store_id, clerk_id, sale.id, [sale_lines[0].id], idem="r1")
    await _return_lines(db_session, store_id, clerk_id, sale.id, [sale_lines[1].id], idem="r2")
    g0401 = [i for i in await einvoice.list_queue(store_id) if i.action is EInvoiceAction.ALLOWANCE]
    assert len(g0401) == 2

    # 第一張核可 → 另一張仍在途 → sale 維持 PENDING_ALLOWANCE。
    await einvoice.drop_pending(
        store_id, g0401[0].id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await einvoice.record_result(store_id, g0401[0].id, success=True)
    mid = await sales.get_sale(store_id, sale.id)
    assert mid is not None
    assert mid.invoice_status is SaleInvoiceStatus.PENDING_ALLOWANCE

    # 第二張也核可 → 全部完成 → 轉正式 ALLOWANCE。
    await einvoice.drop_pending(
        store_id, g0401[1].id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
    )
    await einvoice.record_result(store_id, g0401[1].id, success=True)
    done = await sales.get_sale(store_id, sale.id)
    assert done is not None
    assert done.invoice_status is SaleInvoiceStatus.ALLOWANCE
