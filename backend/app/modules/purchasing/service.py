"""purchasing 業務邏輯：供應商、採購單與一次性收貨入庫。"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import split_tax_inclusive
from app.modules.inventory.service import InventoryService
from app.modules.purchasing.models import (
    GoodsReceipt,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)
from app.modules.purchasing.repository import PurchasingRepository
from app.modules.purchasing.schemas import InputInvoiceIn, PurchaseOrderCreate, SupplierCreate
from app.modules.settings.service import StoreSettingsService
from app.shared.enums import PurchaseOrderStatus
from app.shared.exceptions import (
    CrossStoreReference,
    InputInvoiceAlreadySet,
    InvalidPurchaseOrder,
    PurchaseOrderNotFound,
    PurchaseOrderNotReceivable,
    PurchaseOrderNotReceived,
)


class PurchasingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PurchasingRepository(session)
        self._inventory = InventoryService(session)
        self._settings = StoreSettingsService(session)

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

    @staticmethod
    def _invoice_fields(
        invoice: "InputInvoiceIn", tax_rate: Decimal
    ) -> dict[str, object]:
        """進項發票欄位＋稅額拆分（§6：net = round_ntd(total/(1+rate))、tax = total − net）。"""
        net, tax = split_tax_inclusive(Decimal(invoice.invoice_total), tax_rate)
        return {
            "invoice_number": invoice.invoice_number,
            "invoice_date": invoice.invoice_date,
            "invoice_total": Decimal(invoice.invoice_total),
            "invoice_net": Decimal(net),
            "invoice_tax": Decimal(tax),
        }

    async def register_input_invoice(
        self, store_id: int, purchase_order_id: int, *, invoice: "InputInvoiceIn"
    ) -> GoodsReceipt:
        """補登進項發票（裁示：漏登可事後補登**一次**；已登錄不可覆寫——打錯屬更正流程，另議）。"""
        purchase_order = await self._repo.lock_purchase_order(store_id, purchase_order_id)
        if purchase_order is None:
            raise PurchaseOrderNotFound(f"找不到採購單 {purchase_order_id}")
        receipt = purchase_order.receipt
        if purchase_order.status != PurchaseOrderStatus.RECEIVED or receipt is None:
            raise PurchaseOrderNotReceived(
                f"採購單 {purchase_order_id} 尚未收貨，無法登錄進項發票"
            )
        if receipt.invoice_number is not None:
            raise InputInvoiceAlreadySet(
                f"採購單 {purchase_order_id} 已登錄發票 {receipt.invoice_number}，不可覆寫"
            )
        settings = await self._settings.get_effective_settings(store_id)
        for key, value in self._invoice_fields(invoice, Decimal(settings.tax_rate)).items():
            setattr(receipt, key, value)
        await self._session.flush()
        return receipt

    async def receive_purchase_order(
        self,
        store_id: int,
        purchase_order_id: int,
        *,
        actor_user_id: int,
        invoice: "InputInvoiceIn | None" = None,
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
        invoice_fields: dict[str, object] = {}
        if invoice is not None:
            settings = await self._settings.get_effective_settings(store_id)
            invoice_fields = self._invoice_fields(invoice, Decimal(settings.tax_rate))
        receipt = await self._repo.add_receipt(
            GoodsReceipt(
                store_id=store_id,
                purchase_order_id=purchase_order.id,
                received_by=actor_user_id,
                **invoice_fields,
            )
        )
        purchase_order.status = PurchaseOrderStatus.RECEIVED
        # 剛建立的收貨單（含發票欄）要反映到 po.receipt（selectin 於鎖定查詢時已快取 None）。
        await self._session.flush()
        await self._session.refresh(purchase_order, ["receipt"])
        purchase_order.received_at = datetime.now(UTC)
        purchase_order.received_by = actor_user_id
        await self._session.flush()
        # 收貨 UPDATE 會讓 server onupdate 欄（updated_at）過期；重抓一次（含 lines）取得
        # 已載入的完整列，避免 router 序列化時對過期欄做同步 lazy IO（MissingGreenlet）。
        refreshed = await self._repo.get_purchase_order(store_id, purchase_order.id)
        assert refreshed is not None  # 同交易內剛收貨，必存在
        return refreshed, receipt
