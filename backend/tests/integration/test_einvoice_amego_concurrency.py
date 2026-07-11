"""Amego 開立 × 銷售作廢 併發（Codex 第四輪）：全域鎖序 sale→queue，不得死鎖。

真並行（asyncio.gather）兩個獨立 session：POS 自動開立（issue_for_sale，慢傳輸拉長
持鎖窗口）×經理作廢（void_sale）。修正前 issue_for_sale 先鎖佇列列再鎖 sale，與
「作廢先鎖 sale 再動佇列」AB-BA 死鎖；修正後兩者序列化、皆完成：發票收斂
VOID_PENDING（平台已開 → 續 F0501 作廢）、銷售 VOID。
"""

import asyncio
from decimal import Decimal

from sqlalchemy import delete, select

import app.core.db as app_db
from app.core.audit import AuditLog
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.einvoice.amego import AmegoClient
from app.modules.einvoice.models import EInvoiceResultEvent, EInvoiceUploadQueue, Invoice
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
    Grade,
    InvoiceStatus,
    OwnershipType,
    SaleInvoiceStatus,
    SaleLineType,
    UserRole,
)

_F0401_OK = {
    "code": 0,
    "msg": "",
    "invoice_number": "AB00001111",
    "invoice_time": 1783766130,
    "random_number": "5975",
    "barcode": "11507AB000011115975",
    "qrcode_left": "L",
    "qrcode_right": "R",
}


class _SlowTransport:
    """query 前 sleep 拉長開立的持鎖窗口，讓作廢有時間在中途搶鎖（驗證鎖序）。"""

    async def post_form(self, url: str, form: dict[str, str]) -> dict[str, object]:
        if url.endswith("/json/invoice_query"):
            await asyncio.sleep(0.4)
            return {"code": 9001, "msg": "查無資料"}
        return dict(_F0401_OK)


async def test_issue_and_void_concurrently_no_deadlock() -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發開立店", tax_id="12345678")
        s.add(store)
        await s.flush()
        clerk = User(
            store_id=store.id, username="amego-cc", password_hash="h", role=UserRole.MANAGER
        )
        s.add(clerk)
        await s.flush()
        s.add(StoreSettings(store_id=store.id, einvoice_enabled=True))
        await s.flush()
        await CashDrawerService(s).open_session(store.id, clerk.id, Decimal("1000"))
        item = await InventoryService(s).create_serialized_item(
            store.id,
            item_code="SN-CC-1",
            name="相機",
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
        store_id, clerk_id, sale_id = store.id, clerk.id, sale.id
        await s.commit()

    async def do_issue() -> None:
        async with sm() as s1:
            client = AmegoClient(
                seller_tax_id="12345678",
                app_key="test-key",
                transport=_SlowTransport(),
                base_url="https://invoice-api.amego.tw",
            )
            await EInvoiceService(s1).issue_for_sale(store_id, sale_id, client=client)

    async def do_void() -> None:
        await asyncio.sleep(0.15)  # 開立先持鎖，作廢中途進場
        async with sm() as s2:
            sales = SalesService(s2)
            target = await sales.get_sale_for_update(store_id, sale_id)
            assert target is not None
            await sales.void_sale(target, clerk_id)
            await s2.commit()

    try:
        # 修正前此處死鎖（PG 偵測後 abort 其一 → 例外）；修正後兩者序列化、皆成功。
        await asyncio.gather(do_issue(), do_void())

        async with sm() as s3:
            invoice = await s3.scalar(select(Invoice).where(Invoice.sale_id == sale_id))
            assert invoice is not None
            # 平台已開立、銷售已作廢 → 發票收斂 VOID_PENDING、已排 F0501 續作廢。
            assert invoice.status is InvoiceStatus.VOID_PENDING
            assert invoice.invoice_no == "AB00001111"
            void_items = [
                q
                for q in (
                    await s3.scalars(
                        select(EInvoiceUploadQueue).where(
                            EInvoiceUploadQueue.invoice_id == invoice.id
                        )
                    )
                ).all()
                if q.action is EInvoiceAction.VOID
            ]
            assert len(void_items) == 1
            sale_row = await SalesService(s3).get_sale(store_id, sale_id)
            assert sale_row is not None and sale_row.invoice_status is SaleInvoiceStatus.VOID
    finally:
        # 清理本測試真 commit 的整條鏈（其他測試有全域計數斷言/整表清理，不得留殘料）。
        async with sm() as s4:
            for stmt in (
                delete(EInvoiceResultEvent).where(EInvoiceResultEvent.store_id == store_id),
                delete(EInvoiceUploadQueue).where(EInvoiceUploadQueue.store_id == store_id),
                delete(Invoice).where(Invoice.store_id == store_id),
                delete(AuditLog).where(AuditLog.store_id == store_id),
                delete(SaleTender).where(SaleTender.store_id == store_id),
                delete(SaleLine).where(SaleLine.store_id == store_id),
                delete(StockMovement).where(StockMovement.store_id == store_id),
                delete(CashMovement).where(CashMovement.store_id == store_id),
                delete(Sale).where(Sale.store_id == store_id),
                delete(SerializedItem).where(SerializedItem.store_id == store_id),
                delete(CashSession).where(CashSession.store_id == store_id),
                delete(StoreSettings).where(StoreSettings.store_id == store_id),
                delete(User).where(User.store_id == store_id),
                delete(Store).where(Store.id == store_id),
            ):
                await s4.execute(stmt)
            await s4.commit()
