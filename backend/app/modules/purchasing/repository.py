"""purchasing 資料存取層。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.time import store_date, store_period_end_day
from app.modules.purchasing.models import (
    GoodsReceipt,
    InputInvoice,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)
from app.shared.enums import PurchaseOrderStatus


class PurchasingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def product_referenced(self, store_id: int, catalog_product_id: int) -> bool:
        """這個商品有沒有出現在任何採購明細。"""
        found = await self._session.scalar(
            select(PurchaseOrderLine.id)
            .join(PurchaseOrder, PurchaseOrderLine.purchase_order_id == PurchaseOrder.id)
            .where(
                PurchaseOrder.store_id == store_id,
                PurchaseOrderLine.catalog_product_id == catalog_product_id,
            )
            .limit(1)
        )
        return found is not None

    async def add_supplier(self, supplier: Supplier) -> Supplier:
        self._session.add(supplier)
        await self._session.flush()
        return supplier

    async def get_supplier(self, store_id: int, supplier_id: int) -> Supplier | None:
        stmt = select(Supplier).where(Supplier.id == supplier_id, Supplier.store_id == store_id)
        result: Supplier | None = await self._session.scalar(stmt)
        return result

    async def get_supplier_for_update(self, store_id: int, supplier_id: int) -> Supplier | None:
        """鎖定供應商列（FOR UPDATE）：建單/送出檢查啟用狀態時序列化，擋下並發停用競態。"""
        stmt = (
            select(Supplier)
            .where(Supplier.id == supplier_id, Supplier.store_id == store_id)
            .with_for_update()
        )
        result: Supplier | None = await self._session.scalar(stmt)
        return result

    @staticmethod
    def _suppliers_where(stmt: Any, store_id: int, q: str | None, include_inactive: bool) -> Any:
        """供應商的篩選條件只寫這一份——清單與總筆數共用，兩者才不會各走各的。"""
        stmt = stmt.where(Supplier.store_id == store_id)
        if not include_inactive:
            stmt = stmt.where(Supplier.is_active.is_(True))
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(Supplier.name.ilike(pattern) | Supplier.contact.ilike(pattern))
        return stmt

    async def list_suppliers(
        self,
        store_id: int,
        *,
        q: str | None,
        limit: int,
        offset: int,
        include_inactive: bool = False,
    ) -> list[Supplier]:
        stmt = self._suppliers_where(select(Supplier), store_id, q, include_inactive)
        stmt = stmt.order_by(Supplier.name).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    async def count_suppliers(
        self, store_id: int, *, q: str | None = None, include_inactive: bool = False
    ) -> int:
        """符合同一組篩選的供應商總筆數（不分頁；供應商管理頁算總頁數用）。"""
        stmt = self._suppliers_where(
            select(func.count()).select_from(Supplier), store_id, q, include_inactive
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def add_purchase_order(self, purchase_order: PurchaseOrder) -> PurchaseOrder:
        self._session.add(purchase_order)
        await self._session.flush()
        return purchase_order

    async def add_line(self, line: PurchaseOrderLine) -> PurchaseOrderLine:
        self._session.add(line)
        await self._session.flush()
        return line

    async def latest_received_purchase(
        self, store_id: int, catalog_product_id: int
    ) -> tuple[int, Decimal] | None:
        """某商品「最近一次進貨」的（採購單 id, 進價）；沒有收過貨回 None（docs/70 §4.5）。

        最近＝採購單最後一批收貨時間，再以單號；只算已收數量 > 0 的明細。
        """
        last_receipt = (
            select(func.max(GoodsReceipt.received_at))
            .where(GoodsReceipt.purchase_order_id == PurchaseOrder.id)
            .correlate(PurchaseOrder)
            .scalar_subquery()
        )
        stmt = (
            select(PurchaseOrder.id, PurchaseOrderLine.unit_cost)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .where(
                PurchaseOrderLine.store_id == store_id,
                PurchaseOrderLine.catalog_product_id == catalog_product_id,
                PurchaseOrderLine.received_qty > 0,
            )
            .order_by(last_receipt.desc().nulls_last(), PurchaseOrder.id.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).first()
        return None if row is None else (row[0], row[1])

    async def get_purchase_order(
        self, store_id: int, purchase_order_id: int
    ) -> PurchaseOrder | None:
        stmt = (
            select(PurchaseOrder)
            .options(
                selectinload(PurchaseOrder.lines),
                selectinload(PurchaseOrder.receipts),
            )
            .where(PurchaseOrder.id == purchase_order_id, PurchaseOrder.store_id == store_id)
        )
        result: PurchaseOrder | None = await self._session.scalar(stmt)
        return result

    @staticmethod
    def _purchase_orders_where(
        stmt: Any, store_id: int, statuses: list[PurchaseOrderStatus] | None, q: str | None
    ) -> Any:
        """採購單的篩選條件只寫這一份——清單與總筆數共用，兩者才不會各走各的。"""
        stmt = stmt.where(PurchaseOrder.store_id == store_id)
        if statuses:
            stmt = stmt.where(PurchaseOrder.status.in_(statuses))
        if q:
            needle = q.strip().lstrip("#")
            # 搜尋供應商名快照（ilike，改名不影響歷史搜尋）或單號（純數字精確比對 PO id）。
            conditions: list[ColumnElement[bool]] = [
                PurchaseOrder.supplier_name.ilike(f"%{needle}%")
            ]
            if needle.isdigit():
                conditions.append(PurchaseOrder.id == int(needle))
            stmt = stmt.where(or_(*conditions))
        return stmt

    async def list_purchase_orders(
        self,
        store_id: int,
        *,
        statuses: list[PurchaseOrderStatus] | None = None,
        q: str | None = None,
        limit: int,
        offset: int,
    ) -> list[PurchaseOrder]:
        stmt = self._purchase_orders_where(
            select(PurchaseOrder).options(
                selectinload(PurchaseOrder.lines),
                selectinload(PurchaseOrder.receipts),
            ),
            store_id,
            statuses,
            q,
        )
        stmt = stmt.order_by(PurchaseOrder.id.desc()).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    async def input_invoices_in_period(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> list[InputInvoice]:
        """期間內的進項發票（申報月報；涵蓋的收貨隨 selectin 載入）。

        以**發票日期**歸期（與銷項同口徑）；收貨與發票日期常不同月，用收貨日會錯月。
        界線換算成台灣日曆日（同 einvoice：對 UTC 取 date() 會整條往前挪一天）。
        """
        stmt = (
            select(InputInvoice)
            .where(
                InputInvoice.store_id == store_id,
                InputInvoice.invoice_date >= store_date(date_from),
                InputInvoice.invoice_date <= store_period_end_day(date_to),
            )
            .order_by(InputInvoice.invoice_date, InputInvoice.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def add_input_invoice(self, invoice: InputInvoice) -> InputInvoice:
        self._session.add(invoice)
        await self._session.flush()
        return invoice

    async def get_input_invoice(
        self, store_id: int, invoice_id: int, *, for_update: bool = False
    ) -> InputInvoice | None:
        stmt = select(InputInvoice).where(
            InputInvoice.id == invoice_id, InputInvoice.store_id == store_id
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result: InputInvoice | None = await self._session.scalar(stmt)
        return result

    async def find_input_invoice(
        self, store_id: int, invoice_number: str, invoice_date: date
    ) -> InputInvoice | None:
        stmt = select(InputInvoice).where(
            InputInvoice.store_id == store_id,
            InputInvoice.invoice_number == invoice_number,
            InputInvoice.invoice_date == invoice_date,
        )
        result: InputInvoice | None = await self._session.scalar(stmt)
        return result

    @staticmethod
    def _input_invoices_where(stmt: Any, store_id: int, supplier_id: int | None) -> Any:
        """清單與總筆數共用同一組篩選。"""
        stmt = stmt.where(InputInvoice.store_id == store_id)
        if supplier_id is not None:
            stmt = stmt.where(InputInvoice.supplier_id == supplier_id)
        return stmt

    async def list_input_invoices(
        self, store_id: int, *, supplier_id: int | None, limit: int, offset: int
    ) -> list[InputInvoice]:
        stmt = (
            self._input_invoices_where(select(InputInvoice), store_id, supplier_id)
            .order_by(InputInvoice.invoice_date.desc(), InputInvoice.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_input_invoices(self, store_id: int, *, supplier_id: int | None) -> int:
        stmt = self._input_invoices_where(
            select(func.count()).select_from(InputInvoice), store_id, supplier_id
        )
        return int(await self._session.scalar(stmt) or 0)

    async def receipts_for_update(
        self, store_id: int, receipt_ids: list[int]
    ) -> list[GoodsReceipt]:
        """要掛到發票上的收貨批次（鎖列：兩張發票同時搶同一批時序列化）。"""
        stmt = (
            select(GoodsReceipt)
            .where(GoodsReceipt.store_id == store_id, GoodsReceipt.id.in_(receipt_ids))
            .with_for_update(of=GoodsReceipt)
            .execution_options(populate_existing=True)
        )
        return list((await self._session.scalars(stmt)).all())

    async def uninvoiced_receipts(self, store_id: int, supplier_id: int) -> list[GoodsReceipt]:
        stmt = (
            select(GoodsReceipt)
            .join(PurchaseOrder, PurchaseOrder.id == GoodsReceipt.purchase_order_id)
            .where(
                GoodsReceipt.store_id == store_id,
                GoodsReceipt.input_invoice_id.is_(None),
                PurchaseOrder.supplier_id == supplier_id,
            )
            .order_by(GoodsReceipt.received_at, GoodsReceipt.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def suppliers_of_orders(self, store_id: int, po_ids: list[int]) -> dict[int, int]:
        """{採購單: 供應商}。"""
        if not po_ids:
            return {}
        stmt = select(PurchaseOrder.id, PurchaseOrder.supplier_id).where(
            PurchaseOrder.store_id == store_id, PurchaseOrder.id.in_(po_ids)
        )
        return {int(r[0]): int(r[1]) for r in (await self._session.execute(stmt)).all()}

    async def lines_of_orders(self, store_id: int, po_ids: list[int]) -> list[PurchaseOrderLine]:
        if not po_ids:
            return []
        stmt = select(PurchaseOrderLine).where(
            PurchaseOrderLine.store_id == store_id,
            PurchaseOrderLine.purchase_order_id.in_(po_ids),
        )
        return list((await self._session.scalars(stmt)).all())

    async def receipt_counts(self, store_id: int, po_ids: list[int]) -> dict[int, int]:
        """{採購單: 收貨批數}。"""
        if not po_ids:
            return {}
        stmt = (
            select(GoodsReceipt.purchase_order_id, func.count())
            .where(GoodsReceipt.store_id == store_id, GoodsReceipt.purchase_order_id.in_(po_ids))
            .group_by(GoodsReceipt.purchase_order_id)
        )
        return {int(r[0]): int(r[1]) for r in (await self._session.execute(stmt)).all()}

    async def count_purchase_orders(
        self,
        store_id: int,
        *,
        statuses: list[PurchaseOrderStatus] | None = None,
        q: str | None = None,
    ) -> int:
        """符合同一組篩選的採購單總筆數（不分頁；清單頁算總頁數用）。"""
        stmt = self._purchase_orders_where(
            select(func.count()).select_from(PurchaseOrder), store_id, statuses, q
        )
        return int((await self._session.scalar(stmt)) or 0)

    async def incoming_qty_by_catalog(
        self, store_id: int, catalog_ids: list[int]
    ) -> dict[int, int]:
        """各一般商品的在途待到貨量：Σ(qty − received_qty)，僅計 ORDERED/PARTIAL 採購單。"""
        if not catalog_ids:
            return {}
        stmt = (
            select(
                PurchaseOrderLine.catalog_product_id,
                func.sum(PurchaseOrderLine.qty - PurchaseOrderLine.received_qty),
            )
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .where(
                PurchaseOrderLine.store_id == store_id,
                PurchaseOrderLine.catalog_product_id.in_(catalog_ids),
                PurchaseOrder.status.in_(
                    [PurchaseOrderStatus.ORDERED, PurchaseOrderStatus.PARTIAL]
                ),
            )
            .group_by(PurchaseOrderLine.catalog_product_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {int(cid): int(total or 0) for cid, total in rows}

    async def get_receipt_by_idempotency_key(
        self, store_id: int, idempotency_key: str
    ) -> GoodsReceipt | None:
        stmt = select(GoodsReceipt).where(
            GoodsReceipt.store_id == store_id,
            GoodsReceipt.idempotency_key == idempotency_key,
        )
        result: GoodsReceipt | None = await self._session.scalar(stmt)
        return result

    async def lines_for_catalog(
        self, store_id: int, catalog_product_id: int
    ) -> list[tuple[PurchaseOrderLine, PurchaseOrder]]:
        """某一般商品的所有採購明細＋採購單（庫存明細「進貨歷史」用，新到舊）。
        供應商名取自採購單快照 supplier_name（改名不改寫歷史），故不再 join Supplier。"""
        stmt = (
            select(PurchaseOrderLine, PurchaseOrder)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .where(
                PurchaseOrderLine.store_id == store_id,
                PurchaseOrderLine.catalog_product_id == catalog_product_id,
            )
            .order_by(PurchaseOrder.id.desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [(row[0], row[1]) for row in rows]

    async def lock_purchase_order(
        self, store_id: int, purchase_order_id: int
    ) -> PurchaseOrder | None:
        # FOR UPDATE 只作用於 purchase_orders 主列；selectin 的 lines/receipts 走各自 SELECT。
        # 分批收貨的並發防護靠主列列鎖 + received_qty 原子更新（increment_received_qty）。
        stmt = (
            select(PurchaseOrder)
            .options(
                selectinload(PurchaseOrder.lines),
                selectinload(PurchaseOrder.receipts),
            )
            .where(PurchaseOrder.id == purchase_order_id, PurchaseOrder.store_id == store_id)
            .with_for_update(of=PurchaseOrder)
            .execution_options(populate_existing=True)
        )
        result: PurchaseOrder | None = await self._session.scalar(stmt)
        return result

    async def increment_received_qty(self, store_id: int, line_id: int, delta: int) -> bool:
        """原子累加某明細的已收數量；守衛 received_qty + delta <= qty，成功回 True。"""
        stmt = (
            update(PurchaseOrderLine)
            .where(
                PurchaseOrderLine.id == line_id,
                PurchaseOrderLine.store_id == store_id,
                PurchaseOrderLine.received_qty + delta <= PurchaseOrderLine.qty,
            )
            .values(received_qty=PurchaseOrderLine.received_qty + delta)
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def add_receipt(self, receipt: GoodsReceipt) -> GoodsReceipt:
        self._session.add(receipt)
        await self._session.flush()
        return receipt
