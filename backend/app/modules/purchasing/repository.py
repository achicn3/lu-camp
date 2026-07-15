"""purchasing 資料存取層。"""

from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.purchasing.models import (
    GoodsReceipt,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)
from app.shared.enums import PurchaseOrderStatus


class PurchasingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_supplier(self, supplier: Supplier) -> Supplier:
        self._session.add(supplier)
        await self._session.flush()
        return supplier

    async def get_supplier(self, store_id: int, supplier_id: int) -> Supplier | None:
        stmt = select(Supplier).where(Supplier.id == supplier_id, Supplier.store_id == store_id)
        result: Supplier | None = await self._session.scalar(stmt)
        return result

    async def get_supplier_for_update(
        self, store_id: int, supplier_id: int
    ) -> Supplier | None:
        """鎖定供應商列（FOR UPDATE）：建單/送出檢查啟用狀態時序列化，擋下並發停用競態。"""
        stmt = (
            select(Supplier)
            .where(Supplier.id == supplier_id, Supplier.store_id == store_id)
            .with_for_update()
        )
        result: Supplier | None = await self._session.scalar(stmt)
        return result

    async def list_suppliers(
        self,
        store_id: int,
        *,
        q: str | None,
        limit: int,
        offset: int,
        include_inactive: bool = False,
    ) -> list[Supplier]:
        stmt = select(Supplier).where(Supplier.store_id == store_id)
        if not include_inactive:
            stmt = stmt.where(Supplier.is_active.is_(True))
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(Supplier.name.ilike(pattern) | Supplier.contact.ilike(pattern))
        stmt = stmt.order_by(Supplier.name).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    async def add_purchase_order(self, purchase_order: PurchaseOrder) -> PurchaseOrder:
        self._session.add(purchase_order)
        await self._session.flush()
        return purchase_order

    async def add_line(self, line: PurchaseOrderLine) -> PurchaseOrderLine:
        self._session.add(line)
        await self._session.flush()
        return line

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

    async def list_purchase_orders(
        self,
        store_id: int,
        *,
        statuses: list[PurchaseOrderStatus] | None = None,
        q: str | None = None,
        limit: int,
        offset: int,
    ) -> list[PurchaseOrder]:
        stmt = (
            select(PurchaseOrder)
            .options(
                selectinload(PurchaseOrder.lines),
                selectinload(PurchaseOrder.receipts),
            )
            .where(PurchaseOrder.store_id == store_id)
        )
        if statuses:
            stmt = stmt.where(PurchaseOrder.status.in_(statuses))
        if q:
            needle = q.strip().lstrip("#")
            # 搜尋供應商名（ilike）或單號（純數字精確比對 PO id）。
            conditions: list[ColumnElement[bool]] = [Supplier.name.ilike(f"%{needle}%")]
            if needle.isdigit():
                conditions.append(PurchaseOrder.id == int(needle))
            stmt = stmt.join(Supplier, Supplier.id == PurchaseOrder.supplier_id).where(
                or_(*conditions)
            )
        stmt = stmt.order_by(PurchaseOrder.id.desc()).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    async def incoming_qty_by_catalog(
        self, store_id: int, catalog_ids: list[int]
    ) -> dict[int, int]:
        """各數量品的在途待到貨量：Σ(qty − received_qty)，僅計 ORDERED/PARTIAL 採購單。"""
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
    ) -> list[tuple[PurchaseOrderLine, PurchaseOrder, Supplier]]:
        """某數量品的所有採購明細＋採購單＋供應商（庫存明細「進貨歷史」用，新到舊）。"""
        stmt = (
            select(PurchaseOrderLine, PurchaseOrder, Supplier)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
            .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
            .where(
                PurchaseOrderLine.store_id == store_id,
                PurchaseOrderLine.catalog_product_id == catalog_product_id,
            )
            .order_by(PurchaseOrder.id.desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [(row[0], row[1], row[2]) for row in rows]

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
