"""purchasing 資料存取層。"""

from sqlalchemy import select
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

    async def list_suppliers(
        self, store_id: int, *, q: str | None, limit: int, offset: int
    ) -> list[Supplier]:
        stmt = select(Supplier).where(Supplier.store_id == store_id)
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
            .options(selectinload(PurchaseOrder.lines))
            .where(PurchaseOrder.id == purchase_order_id, PurchaseOrder.store_id == store_id)
        )
        result: PurchaseOrder | None = await self._session.scalar(stmt)
        return result

    async def list_purchase_orders(
        self,
        store_id: int,
        *,
        status: PurchaseOrderStatus | None = None,
        limit: int,
        offset: int,
    ) -> list[PurchaseOrder]:
        stmt = (
            select(PurchaseOrder)
            .options(selectinload(PurchaseOrder.lines))
            .where(PurchaseOrder.store_id == store_id)
        )
        if status is not None:
            stmt = stmt.where(PurchaseOrder.status == status)
        stmt = stmt.order_by(PurchaseOrder.id.desc()).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

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
        stmt = (
            select(PurchaseOrder)
            .options(selectinload(PurchaseOrder.lines))
            .where(PurchaseOrder.id == purchase_order_id, PurchaseOrder.store_id == store_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result: PurchaseOrder | None = await self._session.scalar(stmt)
        return result

    async def add_receipt(self, receipt: GoodsReceipt) -> GoodsReceipt:
        self._session.add(receipt)
        await self._session.flush()
        return receipt
