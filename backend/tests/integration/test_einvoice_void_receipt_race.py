"""einvoice 併發不變量（Codex 第六輪）：銷售作廢 × F0401 回執同時發生，不得死鎖。

全域鎖序 sale → queue：作廢路徑天然先鎖 sale 再鎖佇列；回執路徑（record_result）修正為
先鎖關聯 sale 再鎖佇列。真並行（asyncio.gather 兩條獨立交易）驗證：兩邊都完成、無
DBAPIError（deadlock abort），且收斂到一致終態——不論誰先，發票必為 VOID_PENDING、
恰有一筆 F0501 待送（回執先：ISSUED→作廢排 F0501；作廢先：VOID_PENDING 下 F0401 核可
自動續排 F0501）。
"""

import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, func, select

import app.core.db as app_db
from app.core.audit import AuditLog
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.einvoice.dropper import EInvoiceDropper
from app.modules.einvoice.models import (
    EInvoiceResultEvent,
    EInvoiceUploadQueue,
    Invoice,
)
from app.modules.einvoice.service import EInvoiceService
from app.modules.inventory.models import SerializedItem, StockMovement
from app.modules.inventory.service import InventoryService
from app.modules.sales.inputs import SaleLineInput
from app.modules.sales.models import Sale, SaleLine, SaleTender
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
    SaleLineType,
    UploadStatus,
    UserRole,
)


class _FakeSerializer:
    def serialize_invoice(self, invoice: Invoice, message_type: EInvoiceMessageType) -> bytes:
        return b"<Invoice/>"

    def serialize_allowance(self, allowance: object, message_type: EInvoiceMessageType) -> bytes:
        return b"<Allowance/>"


async def test_void_vs_receipt_race_no_deadlock(tmp_path: Path) -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="發票競態店")
        s.add(store)
        await s.flush()
        clerk = User(
            store_id=store.id, username="einv-race", password_hash="h", role=UserRole.CLERK
        )
        s.add(clerk)
        await s.flush()
        s.add(StoreSettings(store_id=store.id, einvoice_enabled=True))
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("2000"))
        item = await InventoryService(s).create_serialized_item(
            store.id,
            item_code="RACE-1",
            name="競態測試品",
            grade=Grade.A,
            ownership_type=OwnershipType.OWNED,
            listed_price=Decimal(1050),
            acquisition_cost=Decimal(500),
        )
        sale = await SalesService(s).create_sale(
            store.id,
            clerk.id,
            lines=[SaleLineInput(line_type=SaleLineType.SERIALIZED, item_code=item.item_code)],
        )
        einvoice = EInvoiceService(s)
        invoice = await einvoice.get_invoice_for_sale(store.id, sale.id)
        assert invoice is not None
        invoice.invoice_no = "AB12345678"
        invoice.invoice_date = date(2026, 7, 5)
        invoice.invoice_time = "12:34:56"
        invoice.random_number = "1234"
        await s.flush()
        store_id, clerk_id, sale_id, invoice_id = store.id, clerk.id, sale.id, invoice.id
        await s.commit()
        # 拋檔（自管 commit）：F0401 已交付、待回執。
        queue_id = (await einvoice.list_queue(store_id))[0].id
        await einvoice.drop_pending(
            store_id, queue_id, serializer=_FakeSerializer(), dropper=EInvoiceDropper(tmp_path)
        )

    try:

        async def do_void() -> str:
            async with sm() as s:
                svc = SalesService(s)
                target = await svc.get_sale(store_id, sale_id)
                assert target is not None
                await svc.void_sale(target, clerk_id)
                await s.commit()
                return "voided"

        async def do_receipt() -> str:
            async with sm() as s:
                await EInvoiceService(s).record_result(
                    store_id, queue_id, success=True, status_code="0000"
                )
                await s.commit()
                return "accepted"

        # 真並行：鎖序一致（sale→queue）下兩邊序列化完成，不得 deadlock（DBAPIError 會直接讓
        # gather 拋出、測試失敗）。
        results = await asyncio.gather(do_void(), do_receipt())
        assert sorted(results) == ["accepted", "voided"]

        async with sm() as s:
            # 收斂不變量：不論誰先——發票 VOID_PENDING、恰一筆 F0501、F0401 已 UPLOADED。
            inv = await s.get(Invoice, invoice_id)
            assert inv is not None
            assert inv.status is InvoiceStatus.VOID_PENDING
            f0401 = await s.get(EInvoiceUploadQueue, queue_id)
            assert f0401 is not None
            assert f0401.status is UploadStatus.UPLOADED
            f0501_count = await s.scalar(
                select(func.count())
                .select_from(EInvoiceUploadQueue)
                .where(
                    EInvoiceUploadQueue.invoice_id == invoice_id,
                    EInvoiceUploadQueue.action == EInvoiceAction.VOID,
                )
            )
            assert f0501_count == 1
    finally:
        async with sm() as s:
            await s.execute(
                delete(EInvoiceResultEvent).where(EInvoiceResultEvent.store_id == store_id)
            )
            await s.execute(
                delete(EInvoiceUploadQueue).where(EInvoiceUploadQueue.store_id == store_id)
            )
            await s.execute(delete(Invoice).where(Invoice.store_id == store_id))
            await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
            await s.execute(delete(SaleTender).where(SaleTender.store_id == store_id))
            await s.execute(delete(SaleLine).where(SaleLine.store_id == store_id))
            await s.execute(delete(StockMovement).where(StockMovement.store_id == store_id))
            await s.execute(delete(Sale).where(Sale.store_id == store_id))
            await s.execute(delete(SerializedItem).where(SerializedItem.store_id == store_id))
            await s.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
            await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
            await s.execute(delete(StoreSettings).where(StoreSettings.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()
