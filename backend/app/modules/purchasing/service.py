"""purchasing 業務邏輯：供應商、採購單與一次性收貨入庫。"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inventory.service import InventoryService
from app.modules.purchasing.models import (
    GoodsReceipt,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)
from app.modules.purchasing.repository import PurchasingRepository
from app.modules.purchasing.schemas import PurchaseOrderCreate, SupplierCreate
from app.shared.enums import PurchaseOrderStatus
from app.shared.exceptions import (
    CrossStoreReference,
    InvalidPurchaseOrder,
    PurchaseOrderNotFound,
    PurchaseOrderNotReceivable,
)


class PurchasingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PurchasingRepository(session)
        self._inventory = InventoryService(session)

    async def create_supplier(self, store_id: int, payload: SupplierCreate) -> Supplier:
        name = payload.name.strip()
        if not name:
            raise InvalidPurchaseOrder("供應商名稱不可空白")
        supplier = Supplier(
            store_id=store_id,
            name=name,
            contact=payload.contact.strip() if payload.contact else None,
            tax_id=payload.tax_id.strip() if payload.tax_id else None,
        )
        return await self._repo.add_supplier(supplier)

    async def list_suppliers(
        self, store_id: int, *, q: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[Supplier]:
        return await self._repo.list_suppliers(store_id, q=q, limit=limit, offset=offset)

    async def create_purchase_order(
        self,
        store_id: int,
        payload: PurchaseOrderCreate,
        *,
        actor_user_id: int,
    ) -> PurchaseOrder:
        if await self._repo.get_supplier(store_id, payload.supplier_id) is None:
            raise CrossStoreReference(f"供應商 {payload.supplier_id} 不屬於 store {store_id}")
        seen_products: set[int] = set()
        for line in payload.lines:
            if line.catalog_product_id in seen_products:
                raise InvalidPurchaseOrder("同一採購單不可重複同一商品")
            seen_products.add(line.catalog_product_id)
            if await self._inventory.get_catalog(store_id, line.catalog_product_id) is None:
                raise CrossStoreReference(
                    f"數量型商品 {line.catalog_product_id} 不屬於 store {store_id}"
                )

        purchase_order = await self._repo.add_purchase_order(
            PurchaseOrder(
                store_id=store_id,
                supplier_id=payload.supplier_id,
                ordered_by=actor_user_id,
                status=PurchaseOrderStatus.ORDERED,
            )
        )
        for line in payload.lines:
            await self._repo.add_line(
                PurchaseOrderLine(
                    store_id=store_id,
                    purchase_order_id=purchase_order.id,
                    catalog_product_id=line.catalog_product_id,
                    qty=line.qty,
                    unit_cost=line.unit_cost,
                )
            )
        await self._session.refresh(purchase_order, attribute_names=["lines"])
        return purchase_order

    async def list_purchase_orders(
        self,
        store_id: int,
        *,
        status: PurchaseOrderStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PurchaseOrder]:
        return await self._repo.list_purchase_orders(
            store_id, status=status, limit=limit, offset=offset
        )

    async def purchase_history_for_catalog(
        self, store_id: int, catalog_product_id: int
    ) -> list[dict[str, Any]]:
        """某數量品的進貨歷史（供應商/數量/進貨單價/狀態/時間）；庫存明細頁唯讀用。"""
        rows = await self._repo.lines_for_catalog(store_id, catalog_product_id)
        return [
            {
                "po_id": po.id,
                "supplier_id": supplier.id,
                "supplier_name": supplier.name,
                "qty": line.qty,
                "unit_cost": line.unit_cost,
                "status": po.status.value,
                "ordered_at": po.ordered_at,
                "received_at": po.received_at,
            }
            for line, po, supplier in rows
        ]

    async def get_purchase_order(
        self, store_id: int, purchase_order_id: int
    ) -> PurchaseOrder | None:
        return await self._repo.get_purchase_order(store_id, purchase_order_id)

    async def receive_purchase_order(
        self, store_id: int, purchase_order_id: int, *, actor_user_id: int
    ) -> tuple[PurchaseOrder, GoodsReceipt]:
        purchase_order = await self._repo.lock_purchase_order(store_id, purchase_order_id)
        if purchase_order is None:
            raise PurchaseOrderNotFound(f"找不到採購單 {purchase_order_id}")
        if purchase_order.status != PurchaseOrderStatus.ORDERED:
            raise PurchaseOrderNotReceivable(
                f"採購單 {purchase_order_id} 狀態為 {purchase_order.status.value}，不可收貨"
            )
        for line in purchase_order.lines:
            await self._inventory.restock_catalog_items(
                store_id,
                line.catalog_product_id,
                line.qty,
                ref_type="purchase_order",
                ref_id=purchase_order.id,
            )
        receipt = await self._repo.add_receipt(
            GoodsReceipt(
                store_id=store_id,
                purchase_order_id=purchase_order.id,
                received_by=actor_user_id,
            )
        )
        purchase_order.status = PurchaseOrderStatus.RECEIVED
        purchase_order.received_at = datetime.now(UTC)
        purchase_order.received_by = actor_user_id
        await self._session.flush()
        # 收貨 UPDATE 會讓 server onupdate 欄（updated_at）過期；重抓一次（含 lines）取得
        # 已載入的完整列，避免 router 序列化時對過期欄做同步 lazy IO（MissingGreenlet）。
        refreshed = await self._repo.get_purchase_order(store_id, purchase_order.id)
        assert refreshed is not None  # 同交易內剛收貨，必存在
        return refreshed, receipt
