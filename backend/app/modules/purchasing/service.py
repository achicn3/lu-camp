"""purchasing 業務邏輯：供應商、採購單與一次性收貨入庫。"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import split_tax_inclusive
from app.modules.inventory.service import InventoryService
from app.modules.purchasing.models import (
    GoodsReceipt,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)
from app.modules.purchasing.repository import PurchasingRepository
from app.modules.purchasing.schemas import (
    InputInvoiceIn,
    PurchaseOrderCreate,
    ReceiveLineIn,
    SupplierCreate,
    SupplierUpdate,
)
from app.modules.settings.service import StoreSettingsService
from app.shared.enums import PurchaseOrderStatus
from app.shared.exceptions import (
    CrossStoreReference,
    IdempotencyKeyConflict,
    InputInvoiceAlreadySet,
    InvalidPurchaseOrder,
    PurchaseOrderNotCancellable,
    PurchaseOrderNotFound,
    PurchaseOrderNotReceivable,
    PurchaseOrderNotReceived,
    PurchaseOrderNotSubmittable,
    SupplierInactive,
    SupplierNotFound,
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
        self,
        store_id: int,
        *,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
        include_inactive: bool = False,
    ) -> list[Supplier]:
        return await self._repo.list_suppliers(
            store_id, q=q, limit=limit, offset=offset, include_inactive=include_inactive
        )

    async def get_supplier(self, store_id: int, supplier_id: int) -> Supplier:
        supplier = await self._repo.get_supplier(store_id, supplier_id)
        if supplier is None:
            raise SupplierNotFound(f"找不到供應商 {supplier_id}")
        return supplier

    async def update_supplier(
        self, store_id: int, supplier_id: int, payload: "SupplierUpdate", *, actor_user_id: int
    ) -> Supplier:
        """稀疏 PATCH：只更新有帶的欄位（省略維持原值，避免只改名卻清空聯絡/統編）。
        名稱有帶時不可空白；同店重名由唯一約束於 router 轉 409。

        鎖列後才讀原值（get_supplier_for_update）：序列化並發 PATCH，稽核 before 反映真實鎖定前值。
        註：本鎖僅序列化並確保稽核正確，不做樂觀鎖——同欄並發覆寫（後寫者贏）仍可能發生，惟單店
        單機為循序操作不會遇到；日後多終端需嚴格避免，再加版本符記做條件更新回 409。"""
        supplier = await self._repo.get_supplier_for_update(store_id, supplier_id)
        if supplier is None:
            raise SupplierNotFound(f"找不到供應商 {supplier_id}")
        fields = payload.model_fields_set
        if not fields:
            return supplier
        before = {"name": supplier.name, "contact": supplier.contact, "tax_id": supplier.tax_id}
        if "name" in fields:
            name = (payload.name or "").strip()
            if not name:
                raise InvalidPurchaseOrder("供應商名稱不可空白")
            supplier.name = name
        if "contact" in fields:
            supplier.contact = payload.contact.strip() if payload.contact else None
        if "tax_id" in fields:
            supplier.tax_id = payload.tax_id.strip() if payload.tax_id else None
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="UPDATE_SUPPLIER",
            entity_type="supplier",
            entity_id=str(supplier.id),
            before=before,
            after={"name": supplier.name, "contact": supplier.contact, "tax_id": supplier.tax_id},
        )
        # UPDATE 讓 server onupdate 欄（updated_at）過期；重抓以免 router 序列化觸發同步 lazy IO。
        await self._session.refresh(supplier)
        return supplier

    async def set_supplier_active(
        self, store_id: int, supplier_id: int, active: bool, *, actor_user_id: int
    ) -> Supplier:
        """停用/啟用供應商。停用者不進建單選單，但保留供既有採購單歷史參照。"""
        # 鎖列：與建單/送出的啟用檢查序列化，避免「停用 vs 建單」競態繞過控制。
        supplier = await self._repo.get_supplier_for_update(store_id, supplier_id)
        if supplier is None:
            raise SupplierNotFound(f"找不到供應商 {supplier_id}")
        if supplier.is_active == active:
            return supplier
        supplier.is_active = active
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="ACTIVATE_SUPPLIER" if active else "DEACTIVATE_SUPPLIER",
            entity_type="supplier",
            entity_id=str(supplier.id),
            before={"is_active": not active},
            after={"is_active": active},
        )
        # UPDATE 讓 server onupdate 欄（updated_at）過期；重抓以免 router 序列化觸發同步 lazy IO。
        await self._session.refresh(supplier)
        return supplier

    async def create_purchase_order(
        self,
        store_id: int,
        payload: PurchaseOrderCreate,
        *,
        actor_user_id: int,
    ) -> PurchaseOrder:
        # 鎖定供應商列：擋下「並發停用 vs 建單」競態，並禁止用停用中的供應商建單（後端強制，
        # 不僅靠前端選單隱藏——停用才是可執行的業務控制，Codex 對抗審 high）。
        supplier = await self._repo.get_supplier_for_update(store_id, payload.supplier_id)
        if supplier is None:
            raise CrossStoreReference(f"供應商 {payload.supplier_id} 不屬於 store {store_id}")
        if not supplier.is_active:
            raise SupplierInactive(f"供應商 {payload.supplier_id} 已停用，不可用於新採購單")
        seen_products: set[int] = set()
        for line in payload.lines:
            if line.catalog_product_id in seen_products:
                raise InvalidPurchaseOrder("同一採購單不可重複同一商品")
            seen_products.add(line.catalog_product_id)
            if await self._inventory.get_catalog(store_id, line.catalog_product_id) is None:
                raise CrossStoreReference(
                    f"一般商品 {line.catalog_product_id} 不屬於 store {store_id}"
                )

        # 預設建為草稿；payload.submit=True 則建立即送出（ORDERED、計入待到貨、可收貨）。
        status = PurchaseOrderStatus.ORDERED if payload.submit else PurchaseOrderStatus.DRAFT
        purchase_order = await self._repo.add_purchase_order(
            PurchaseOrder(
                store_id=store_id,
                supplier_id=payload.supplier_id,
                supplier_name=supplier.name,  # 快照下單當下的供應商名（改名不改寫歷史）
                ordered_by=actor_user_id,
                status=status,
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

    async def submit_purchase_order(
        self, store_id: int, purchase_order_id: int, *, actor_user_id: int
    ) -> PurchaseOrder:
        """草稿送出 → ORDERED（計入待到貨、可收貨）。僅草稿可送出。"""
        purchase_order = await self._repo.lock_purchase_order(store_id, purchase_order_id)
        if purchase_order is None:
            raise PurchaseOrderNotFound(f"找不到採購單 {purchase_order_id}")
        if purchase_order.status != PurchaseOrderStatus.DRAFT:
            raise PurchaseOrderNotSubmittable(
                f"採購單 {purchase_order_id} 狀態為 {purchase_order.status.value}，僅草稿可送出"
            )
        # 草稿建立後供應商可能已被停用：送出前重新驗證（鎖列序列化並發停用）。
        supplier = await self._repo.get_supplier_for_update(store_id, purchase_order.supplier_id)
        if supplier is None or not supplier.is_active:
            raise SupplierInactive(
                f"採購單 {purchase_order_id} 的供應商已停用，不可送出；請改供應商或重新啟用"
            )
        purchase_order.status = PurchaseOrderStatus.ORDERED
        # 正式下單時間/下單人/供應商名快照皆以「送出」當下為準：草稿期間供應商若改名，
        # 送出後的正式單以送出當下的名為準（Codex 對抗審 medium）。
        purchase_order.ordered_at = datetime.now(UTC)
        purchase_order.ordered_by = actor_user_id
        purchase_order.supplier_name = supplier.name
        await self._session.flush()
        refreshed = await self._repo.get_purchase_order(store_id, purchase_order.id)
        assert refreshed is not None
        return refreshed

    async def cancel_purchase_order(
        self, store_id: int, purchase_order_id: int, *, actor_user_id: int
    ) -> PurchaseOrder:
        """取消採購單 → CANCELLED。僅草稿/已下單且尚未收任何貨可取消（部分/已收貨不可）。"""
        purchase_order = await self._repo.lock_purchase_order(store_id, purchase_order_id)
        if purchase_order is None:
            raise PurchaseOrderNotFound(f"找不到採購單 {purchase_order_id}")
        if purchase_order.status not in (
            PurchaseOrderStatus.DRAFT,
            PurchaseOrderStatus.ORDERED,
        ):
            raise PurchaseOrderNotCancellable(
                f"採購單 {purchase_order_id} 狀態為 {purchase_order.status.value}，"
                "僅草稿/已下單且尚未收貨可取消"
            )
        before = purchase_order.status.value
        purchase_order.status = PurchaseOrderStatus.CANCELLED
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="CANCEL_PURCHASE_ORDER",
            entity_type="purchase_order",
            entity_id=str(purchase_order.id),
            before={"status": before},
            after={"status": PurchaseOrderStatus.CANCELLED.value},
        )
        refreshed = await self._repo.get_purchase_order(store_id, purchase_order.id)
        assert refreshed is not None
        return refreshed

    async def list_purchase_orders(
        self,
        store_id: int,
        *,
        statuses: list[PurchaseOrderStatus] | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PurchaseOrder]:
        return await self._repo.list_purchase_orders(
            store_id, statuses=statuses, q=q, limit=limit, offset=offset
        )

    async def incoming_qty_by_catalog(
        self, store_id: int, catalog_ids: list[int]
    ) -> dict[int, int]:
        """各一般商品在途待到貨量（Σ 未收完採購單的 訂購−已收）；供低庫存提醒避免重複採購。"""
        return await self._repo.incoming_qty_by_catalog(store_id, catalog_ids)

    async def purchase_history_for_catalog(
        self, store_id: int, catalog_product_id: int
    ) -> list[dict[str, Any]]:
        """某一般商品的進貨歷史（供應商/數量/進貨單價/狀態/時間）；庫存明細頁唯讀用。"""
        rows = await self._repo.lines_for_catalog(store_id, catalog_product_id)
        return [
            {
                "po_id": po.id,
                "supplier_id": po.supplier_id,
                "supplier_name": po.supplier_name,  # 下單當下快照（改名不改寫歷史）
                "qty": line.qty,
                "received_qty": line.received_qty,
                "unit_cost": line.unit_cost,
                "status": po.status.value,
                "ordered_at": po.ordered_at,
                "received_at": po.received_at,
            }
            for line, po in rows
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
        self,
        store_id: int,
        purchase_order_id: int,
        receipt_id: int,
        *,
        invoice: "InputInvoiceIn",
    ) -> GoodsReceipt:
        """補登某收貨批次的進項發票（漏登可事後補登；已登錄不可覆寫——打錯屬更正流程，另議）。"""
        purchase_order = await self._repo.lock_purchase_order(store_id, purchase_order_id)
        if purchase_order is None:
            raise PurchaseOrderNotFound(f"找不到採購單 {purchase_order_id}")
        receipt = next((r for r in purchase_order.receipts if r.id == receipt_id), None)
        if receipt is None:
            raise PurchaseOrderNotReceived(
                f"採購單 {purchase_order_id} 無收貨批次 {receipt_id}，無法登錄進項發票"
            )
        if receipt.invoice_number is not None:
            raise InputInvoiceAlreadySet(
                f"收貨批次 {receipt_id} 已登錄發票 {receipt.invoice_number}，不可覆寫"
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
        lines: list["ReceiveLineIn"],
        idempotency_key: str,
        request_fingerprint: str,
        invoice: "InputInvoiceIn | None" = None,
    ) -> tuple[PurchaseOrder, GoodsReceipt]:
        """分批收貨：對指定明細各收 qty（不得超過待收），更新庫存＋寫庫存異動，
        建立一張收貨批次（可選填進項發票），並依是否全數收足轉 PARTIAL/RECEIVED。

        冪等（防網路重試重複入庫）：同店同 idempotency_key 只成立一筆收貨——重送且指紋相符回原
        結果、不重複加庫存；指紋不符 → 409。並行首寫競態由唯一索引擋下（router 收攏回放）。
        """
        # 前置回放：同 key 已有收貨 → 指紋相符回原結果、不同 → 衝突。
        existing = await self._repo.get_receipt_by_idempotency_key(store_id, idempotency_key)
        if existing is not None:
            if (
                existing.purchase_order_id == purchase_order_id
                and existing.request_fingerprint == request_fingerprint
            ):
                replayed = await self._repo.get_purchase_order(store_id, purchase_order_id)
                assert replayed is not None
                return replayed, existing
            raise IdempotencyKeyConflict("Idempotency-Key 已用於不同的收貨請求")

        purchase_order = await self._repo.lock_purchase_order(store_id, purchase_order_id)
        if purchase_order is None:
            raise PurchaseOrderNotFound(f"找不到採購單 {purchase_order_id}")
        if purchase_order.status not in (
            PurchaseOrderStatus.ORDERED,
            PurchaseOrderStatus.PARTIAL,
        ):
            raise PurchaseOrderNotReceivable(
                f"採購單 {purchase_order_id} 狀態為 {purchase_order.status.value}，不可收貨"
            )
        line_by_id = {line.id: line for line in purchase_order.lines}
        # 驗證：明細須屬本單、不得重複、qty 不得超過待收（qty − received_qty）。
        seen: set[int] = set()
        to_receive: list[tuple[PurchaseOrderLine, int]] = []
        for item in lines:
            if item.line_id in seen:
                raise InvalidPurchaseOrder(f"收貨明細重複：line {item.line_id}")
            seen.add(item.line_id)
            po_line = line_by_id.get(item.line_id)
            if po_line is None:
                raise InvalidPurchaseOrder(
                    f"明細 {item.line_id} 不屬於採購單 {purchase_order_id}"
                )
            remaining = po_line.qty - po_line.received_qty
            if item.qty > remaining:
                raise InvalidPurchaseOrder(
                    f"明細 {item.line_id} 本次收 {item.qty} 超過待收 {remaining}"
                )
            to_receive.append((po_line, item.qty))
        if not to_receive:
            raise InvalidPurchaseOrder("收貨至少需一筆明細")

        invoice_fields: dict[str, object] = {}
        if invoice is not None:
            settings = await self._settings.get_effective_settings(store_id)
            invoice_fields = self._invoice_fields(invoice, Decimal(settings.tax_rate))
        # 先建收貨批次取得 id，庫存異動以 ref_type="goods_receipt" 指向本批。
        # 冪等鍵/指紋落在同一交易：並行首寫互撞由 (store, key) 唯一索引擋下（router 回放）。
        receipt = await self._repo.add_receipt(
            GoodsReceipt(
                store_id=store_id,
                purchase_order_id=purchase_order.id,
                received_by=actor_user_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                **invoice_fields,
            )
        )
        for po_line, qty in to_receive:
            await self._inventory.restock_catalog_items(
                store_id,
                po_line.catalog_product_id,
                qty,
                ref_type="goods_receipt",
                ref_id=receipt.id,
            )
            ok = await self._repo.increment_received_qty(store_id, po_line.id, qty)
            if not ok:  # 併發下另一交易先收了；主列列鎖下不應發生，防禦性守衛。
                raise PurchaseOrderNotReceivable(
                    f"明細 {po_line.id} 收貨數量超過待收（併發衝突），請重試"
                )

        await self._session.flush()
        # 重載 lines（received_qty 由 bulk UPDATE 改動）與 receipts（鎖定時載入為空、剛新增一筆），
        # 否則 identity-map 內已載入的空 receipts 集合不會被後續 SELECT 覆寫。
        await self._session.refresh(purchase_order, ["lines", "receipts"])
        fully = all(line.received_qty >= line.qty for line in purchase_order.lines)
        purchase_order.status = (
            PurchaseOrderStatus.RECEIVED if fully else PurchaseOrderStatus.PARTIAL
        )
        if fully:
            purchase_order.received_at = datetime.now(UTC)
            purchase_order.received_by = actor_user_id
        await self._session.flush()
        # 重抓完整列（含 lines/receipts），避免 router 序列化觸發同步 lazy IO（MissingGreenlet）。
        refreshed = await self._repo.get_purchase_order(store_id, purchase_order.id)
        assert refreshed is not None
        return refreshed, receipt
