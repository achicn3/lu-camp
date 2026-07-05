"""einvoice 資料存取層（唯一直接碰 ORM 的層）。

佇列列以 SELECT … FOR UPDATE 取得（拋檔/回執狀態變更序列化錨點，沿 D-1 模式），
避免同一列被並發拋檔/標記造成 attempts 或狀態競態。
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.einvoice.models import (
    EInvoiceResultEvent,
    EInvoiceUploadQueue,
    Invoice,
    InvoiceAllowance,
)
from app.shared.enums import UploadStatus


class EInvoiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── 發票 ──

    async def add_invoice(self, invoice: Invoice) -> Invoice:
        self._session.add(invoice)
        await self._session.flush()
        return invoice

    async def get_invoice(self, store_id: int, invoice_id: int) -> Invoice | None:
        stmt = select(Invoice).where(Invoice.id == invoice_id, Invoice.store_id == store_id)
        result: Invoice | None = await self._session.scalar(stmt)
        return result

    async def find_invoice_by_sale(self, store_id: int, sale_id: int) -> Invoice | None:
        """以 sale_id 找既有發票（一筆銷售至多一張發票，冪等重入用）。"""
        stmt = select(Invoice).where(Invoice.store_id == store_id, Invoice.sale_id == sale_id)
        result: Invoice | None = await self._session.scalar(stmt)
        return result

    # ── 折讓 ──

    async def add_allowance(self, allowance: InvoiceAllowance) -> InvoiceAllowance:
        self._session.add(allowance)
        await self._session.flush()
        return allowance

    async def sum_allowances_total(self, store_id: int, invoice_id: int) -> Decimal:
        """某發票已開折讓的累計金額（供超額守衛：Σ 折讓 + 本次 ≤ 發票總額）。"""
        stmt = select(func.coalesce(func.sum(InvoiceAllowance.total), 0)).where(
            InvoiceAllowance.store_id == store_id,
            InvoiceAllowance.invoice_id == invoice_id,
        )
        value = await self._session.scalar(stmt)
        return Decimal(value if value is not None else 0)

    async def find_allowance_by_return(
        self, store_id: int, return_id: int
    ) -> InvoiceAllowance | None:
        """以退貨單找既有折讓（一退貨至多一折讓；重呼防重複）。"""
        stmt = select(InvoiceAllowance).where(
            InvoiceAllowance.store_id == store_id,
            InvoiceAllowance.return_id == return_id,
        )
        result: InvoiceAllowance | None = await self._session.scalar(stmt)
        return result

    # ── 佇列 ──

    async def add_queue_item(self, item: EInvoiceUploadQueue) -> EInvoiceUploadQueue:
        self._session.add(item)
        await self._session.flush()
        return item

    async def list_queue_items_for_invoice(
        self, store_id: int, invoice_id: int
    ) -> list[EInvoiceUploadQueue]:
        """某發票的所有佇列列（作廢時中止其待送 F0401 用）。"""
        stmt = select(EInvoiceUploadQueue).where(
            EInvoiceUploadQueue.store_id == store_id,
            EInvoiceUploadQueue.invoice_id == invoice_id,
        )
        return list((await self._session.scalars(stmt)).all())

    async def lock_queue_items_for_invoice(
        self, store_id: int, invoice_id: int
    ) -> list[EInvoiceUploadQueue]:
        """某發票的所有佇列列（FOR UPDATE、刷新到已提交狀態）。

        作廢判斷「取消 vs 在途」必須與交付協議（_expose_and_confirm 持列鎖寫檔）同鎖序列化，
        否則可能讀到過期的未認領列、在另一 worker 曝光檔案後才取消（Codex 第五輪）。
        """
        stmt = (
            select(EInvoiceUploadQueue)
            .where(
                EInvoiceUploadQueue.store_id == store_id,
                EInvoiceUploadQueue.invoice_id == invoice_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_other_pending_allowance_items(
        self, store_id: int, invoice_id: int, *, exclude_queue_id: int
    ) -> int:
        """同一發票「其他」仍待送/待回執的折讓佇列列數（多折讓 in-flight 時 sale 級狀態的精度）。"""
        stmt = (
            select(func.count())
            .select_from(EInvoiceUploadQueue)
            .join(
                InvoiceAllowance,
                InvoiceAllowance.id == EInvoiceUploadQueue.allowance_id,
            )
            .where(
                EInvoiceUploadQueue.store_id == store_id,
                EInvoiceUploadQueue.status == UploadStatus.PENDING,
                EInvoiceUploadQueue.id != exclude_queue_id,
                InvoiceAllowance.invoice_id == invoice_id,
            )
        )
        value = await self._session.scalar(stmt)
        return int(value if value is not None else 0)

    async def lock_queue_item(self, store_id: int, queue_id: int) -> EInvoiceUploadQueue | None:
        """取得佇列列並上 row lock（拋檔/標記/重送前重載持久列，不信任呼叫端物件）。"""
        stmt = (
            select(EInvoiceUploadQueue)
            .where(
                EInvoiceUploadQueue.id == queue_id,
                EInvoiceUploadQueue.store_id == store_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result: EInvoiceUploadQueue | None = await self._session.scalar(stmt)
        return result

    async def list_queue(
        self,
        store_id: int,
        *,
        status: UploadStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EInvoiceUploadQueue]:
        stmt = select(EInvoiceUploadQueue).where(EInvoiceUploadQueue.store_id == store_id)
        if status is not None:
            stmt = stmt.where(EInvoiceUploadQueue.status == status)
        stmt = stmt.order_by(EInvoiceUploadQueue.id.desc()).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    # ── 回執事件 ──

    async def add_result_event(self, event: EInvoiceResultEvent) -> EInvoiceResultEvent:
        self._session.add(event)
        await self._session.flush()
        return event
