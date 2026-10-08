"""purchasing 業務邏輯：供應商、採購單與一次性收貨入庫。"""

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import MAX_NTD
from app.modules.inventory.service import InventoryService
from app.modules.purchasing.models import (
    GoodsReceipt,
    InputInvoice,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)
from app.modules.purchasing.repository import PurchasingRepository
from app.modules.purchasing.schemas import (
    InputInvoiceIn,
    InputInvoiceWrite,
    PurchaseOrderCreate,
    PurchaseOrderUpdate,
    ReceiveLineIn,
    SupplierCreate,
    SupplierUpdate,
)
from app.shared.enums import PurchaseOrderStatus
from app.shared.exceptions import (
    CrossStoreReference,
    DuplicateInputInvoice,
    IdempotencyKeyConflict,
    InputInvoiceAlreadySet,
    InputInvoiceInvalid,
    InputInvoiceNotFound,
    InvalidPurchaseOrder,
    PurchaseOrderNotCancellable,
    PurchaseOrderNotEditable,
    PurchaseOrderNotFound,
    PurchaseOrderNotReceivable,
    PurchaseOrderNotSubmittable,
    PurchasingManagerOnly,
    SupplierInactive,
    SupplierNotFound,
)

_NONE_RECEIVED: tuple[int, Decimal] = (0, Decimal(0))


class PurchasingService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PurchasingRepository(session)
        self._inventory = InventoryService(session)

    async def product_referenced(self, store_id: int, catalog_product_id: int) -> bool:
        """供庫存判斷可不可以刪：這個商品有沒有採購紀錄。"""
        return await self._repo.product_referenced(store_id, catalog_product_id)

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

    async def count_suppliers(
        self, store_id: int, *, q: str | None = None, include_inactive: bool = False
    ) -> int:
        """符合同一組篩選的供應商總筆數（清單頁算總頁數用）。"""
        return await self._repo.count_suppliers(store_id, q=q, include_inactive=include_inactive)

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
        # service 也守一次，避免內部呼叫以 model_construct 繞過 API schema 後，直到 DB flush
        # 才得到 Numeric overflow 的 500。所有檢查都在主檔/明細寫入之前。
        for line in payload.lines:
            unit_cost = Decimal(line.unit_cost)
            if line.qty <= 0 or unit_cost <= 0 or unit_cost != unit_cost.to_integral_value():
                raise InvalidPurchaseOrder("採購數量與單價必須為正整數")
            if unit_cost > MAX_NTD:
                raise InvalidPurchaseOrder(f"採購單價不可超過資料庫金額上限 {MAX_NTD}")
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

    async def update_purchase_order(
        self,
        store_id: int,
        purchase_order_id: int,
        payload: PurchaseOrderUpdate,
        *,
        actor_user_id: int,
        actor_is_manager: bool,
    ) -> PurchaseOrder:
        """整張覆寫採購單（docs/70 §4）：供應商、明細（改／加／刪）、已收數量。

        草稿全員可改；已下單／部分到貨／已收貨只有管理者。已收數量的差額逐商品加減庫存
        （扣不夠整筆擋下）；最近一次進貨是這張單的商品同步成本；前後值寫稽核。
        """
        purchase_order = await self._repo.lock_purchase_order(store_id, purchase_order_id)
        if purchase_order is None:
            raise PurchaseOrderNotFound(f"找不到採購單 {purchase_order_id}")
        if purchase_order.status == PurchaseOrderStatus.CANCELLED:
            raise PurchaseOrderNotEditable(f"採購單 {purchase_order_id} 已取消，不能修改")
        if purchase_order.status != PurchaseOrderStatus.DRAFT and not actor_is_manager:
            raise PurchasingManagerOnly("已下單或已收貨的採購單只有管理者能修改")
        has_receipts = purchase_order.status in (
            PurchaseOrderStatus.PARTIAL,
            PurchaseOrderStatus.RECEIVED,
        )
        existing = {line.id: line for line in purchase_order.lines}
        await self._validate_edit(store_id, payload, existing, has_receipts=has_receipts)
        if payload.supplier_id != purchase_order.supplier_id:
            await self._change_supplier(store_id, purchase_order, payload.supplier_id)

        before = self._edit_snapshot(purchase_order)
        old = self._received_by_product(
            (line.catalog_product_id, line.received_qty, line.unit_cost)
            for line in purchase_order.lines
        )
        new = self._received_by_product(
            (line.catalog_product_id, line.received_qty, Decimal(line.unit_cost))
            for line in payload.lines
        )
        affected = sorted(p for p in old.keys() | new.keys() if old.get(p) != new.get(p))
        # 「改前最近一次進貨」要在明細寫進去之前讀。
        latest_before = {
            p: await self._repo.latest_received_purchase(store_id, p) for p in affected
        }
        self._apply_lines(store_id, purchase_order, payload, existing)
        await self._session.flush()

        for product_id in affected:
            delta = new.get(product_id, _NONE_RECEIVED)[0] - old.get(product_id, _NONE_RECEIVED)[0]
            await self._inventory.correct_purchased_stock(
                store_id, product_id, delta, ref_type="purchase_order", ref_id=purchase_order.id
            )
        if has_receipts:
            self._restate_status(purchase_order, actor_user_id)
        await self._session.flush()
        await self._resync_costs(store_id, purchase_order.id, latest_before)

        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="UPDATE_PURCHASE_ORDER",
            entity_type="purchase_order",
            entity_id=str(purchase_order.id),
            before=before,
            after=self._edit_snapshot(purchase_order),
        )
        refreshed = await self._repo.get_purchase_order(store_id, purchase_order.id)
        assert refreshed is not None
        return refreshed

    async def _validate_edit(
        self,
        store_id: int,
        payload: PurchaseOrderUpdate,
        existing: dict[int, PurchaseOrderLine],
        *,
        has_receipts: bool,
    ) -> None:
        seen_products: set[int] = set()
        seen_lines: set[int] = set()
        for line in payload.lines:
            unit_cost = Decimal(line.unit_cost)
            if line.qty <= 0 or unit_cost <= 0 or unit_cost != unit_cost.to_integral_value():
                raise InvalidPurchaseOrder("採購數量與單價必須為正整數")
            if unit_cost > MAX_NTD:
                raise InvalidPurchaseOrder(f"採購單價不可超過資料庫金額上限 {MAX_NTD}")
            if not 0 <= line.received_qty <= line.qty:
                raise InvalidPurchaseOrder("已收數量必須介於 0 與訂購數量之間")
            if line.received_qty and not has_receipts:
                raise InvalidPurchaseOrder("還沒收過貨的採購單不能填已收數量，請用「收貨入庫」")
            if line.catalog_product_id in seen_products:
                raise InvalidPurchaseOrder("同一採購單不可重複同一商品")
            seen_products.add(line.catalog_product_id)
            if line.id is not None:
                if line.id not in existing or line.id in seen_lines:
                    raise InvalidPurchaseOrder(f"明細 {line.id} 不屬於這張採購單")
                seen_lines.add(line.id)
            if await self._inventory.get_catalog(store_id, line.catalog_product_id) is None:
                raise CrossStoreReference(
                    f"一般商品 {line.catalog_product_id} 不屬於 store {store_id}"
                )

    async def _change_supplier(
        self, store_id: int, purchase_order: PurchaseOrder, supplier_id: int
    ) -> None:
        supplier = await self._repo.get_supplier_for_update(store_id, supplier_id)
        if supplier is None:
            raise CrossStoreReference(f"供應商 {supplier_id} 不屬於 store {store_id}")
        if not supplier.is_active:
            raise SupplierInactive(f"供應商「{supplier.name}」已停用，不能改成它")
        if any(receipt.input_invoice_id is not None for receipt in purchase_order.receipts):
            raise InvalidPurchaseOrder(
                f"這張採購單的收貨已登在「{purchase_order.supplier_name}」的進項發票上，"
                "請先到進項發票把這幾批移除，再換供應商"
            )
        purchase_order.supplier_id = supplier.id
        purchase_order.supplier_name = supplier.name

    @staticmethod
    def _apply_lines(
        store_id: int,
        purchase_order: PurchaseOrder,
        payload: PurchaseOrderUpdate,
        existing: dict[int, PurchaseOrderLine],
    ) -> None:
        kept = {line.id for line in payload.lines if line.id is not None}
        for line in [ln for ln in purchase_order.lines if ln.id not in kept]:
            purchase_order.lines.remove(line)  # delete-orphan 會刪掉這列
        for item in payload.lines:
            if item.id is not None:
                target = existing[item.id]
                target.catalog_product_id = item.catalog_product_id
                target.qty = item.qty
                target.received_qty = item.received_qty
                target.unit_cost = Decimal(item.unit_cost)
            else:
                purchase_order.lines.append(
                    PurchaseOrderLine(
                        store_id=store_id,
                        catalog_product_id=item.catalog_product_id,
                        qty=item.qty,
                        received_qty=item.received_qty,
                        unit_cost=Decimal(item.unit_cost),
                    )
                )

    @staticmethod
    def _received_by_product(
        lines: Iterable[tuple[int, int, Decimal]],
    ) -> dict[int, tuple[int, Decimal]]:
        """（商品, 已收, 進價）→ {商品: (已收, 進價)}；只列已收 > 0 的。"""
        return {product: (received, cost) for product, received, cost in lines if received > 0}

    @staticmethod
    def _restate_status(purchase_order: PurchaseOrder, actor_user_id: int) -> None:
        fully = all(line.received_qty >= line.qty for line in purchase_order.lines)
        purchase_order.status = (
            PurchaseOrderStatus.RECEIVED if fully else PurchaseOrderStatus.PARTIAL
        )
        if not fully:
            purchase_order.received_at = None
            purchase_order.received_by = None
        elif purchase_order.received_at is None:
            purchase_order.received_at = datetime.now(UTC)
            purchase_order.received_by = actor_user_id

    async def _resync_costs(
        self,
        store_id: int,
        purchase_order_id: int,
        latest_before: dict[int, tuple[int, Decimal] | None],
    ) -> None:
        """改前或改後最近一次進貨是這張單，商品成本就設為改後最近一次進貨的進價（docs/70 §4.5）。"""
        for product_id, before in latest_before.items():
            after = await self._repo.latest_received_purchase(store_id, product_id)
            touched = (before is not None and before[0] == purchase_order_id) or (
                after is not None and after[0] == purchase_order_id
            )
            if touched and after is not None:
                await self._inventory.set_catalog_cost(store_id, product_id, after[1])

    @staticmethod
    def _edit_snapshot(purchase_order: PurchaseOrder) -> dict[str, Any]:
        return {
            "status": purchase_order.status.value,
            "supplier_id": purchase_order.supplier_id,
            "lines": [
                {
                    "catalog_product_id": line.catalog_product_id,
                    "qty": line.qty,
                    "received_qty": line.received_qty,
                    "unit_cost": str(line.unit_cost),
                }
                for line in purchase_order.lines
            ],
        }

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

    async def input_invoices_in_period(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> list[InputInvoice]:
        """期間內的進項發票（申報月報；跨模組供 reports 用，§2 經 service）。"""
        return await self._repo.input_invoices_in_period(store_id, date_from, date_to)

    async def count_purchase_orders(
        self,
        store_id: int,
        *,
        statuses: list[PurchaseOrderStatus] | None = None,
        q: str | None = None,
    ) -> int:
        """符合同一組篩選的採購單總筆數（清單頁算總頁數用）。"""
        return await self._repo.count_purchase_orders(store_id, statuses=statuses, q=q)

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

    async def receive_purchase_order(
        self,
        store_id: int,
        purchase_order_id: int,
        *,
        actor_user_id: int,
        lines: list["ReceiveLineIn"],
        idempotency_key: str,
        invoice: "InputInvoiceIn | None" = None,
    ) -> tuple[PurchaseOrder, GoodsReceipt]:
        """分批收貨：對指定明細各收 qty（不得超過待收），更新庫存＋寫庫存異動，
        建立一張收貨批次（可選填進項發票），並依是否全數收足轉 PARTIAL/RECEIVED。

        冪等（防網路重試重複入庫）：同店同 idempotency_key 只成立一筆收貨——重送且指紋相符回原
        結果、不重複加庫存；指紋不符 → 409。並行首寫競態由唯一索引擋下（router 收攏回放）。
        """
        request_fingerprint = self._receive_fingerprint(
            purchase_order_id,
            lines=lines,
            invoice=invoice,
        )
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
                raise InvalidPurchaseOrder(f"明細 {item.line_id} 不屬於採購單 {purchase_order_id}")
            remaining = po_line.qty - po_line.received_qty
            if item.qty > remaining:
                raise InvalidPurchaseOrder(
                    f"明細 {item.line_id} 本次收 {item.qty} 超過待收 {remaining}"
                )
            to_receive.append((po_line, item.qty))
        if not to_receive:
            raise InvalidPurchaseOrder("收貨至少需一筆明細")

        # 隨貨發票：同供應商已登過同一張（整月合併開）就掛上去，否則新建一張只掛這批。
        existing_invoice = (
            None
            if invoice is None
            else await self._matching_invoice(store_id, purchase_order.supplier_id, invoice)
        )
        # 先建收貨批次取得 id，庫存異動以 ref_type="goods_receipt" 指向本批。
        # 冪等鍵/指紋落在同一交易：並行首寫互撞由 (store, key) 唯一索引擋下（router 回放）。
        receipt = await self._repo.add_receipt(
            GoodsReceipt(
                store_id=store_id,
                purchase_order_id=purchase_order.id,
                received_by=actor_user_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
        )
        if invoice is not None:
            target = existing_invoice or await self._repo.add_input_invoice(
                InputInvoice(
                    store_id=store_id,
                    supplier_id=purchase_order.supplier_id,
                    supplier_name=purchase_order.supplier_name,
                    created_by=actor_user_id,
                    **self._invoice_values(invoice),
                )
            )
            receipt.input_invoice_id = target.id
        for po_line, qty in to_receive:
            await self._inventory.restock_catalog_items(
                store_id,
                po_line.catalog_product_id,
                qty,
                ref_type="goods_receipt",
                ref_id=receipt.id,
                # 收貨時把商品成本更新為**本次進價**（裁示：最新進價）。
                # 這是 catalog 成本唯一的來源；沒有它，贈品成本與貢獻毛利永遠是 0。
                unit_cost=po_line.unit_cost,
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

    # ── 進項發票（docs/70 §5）────────────────────────────────────────

    @staticmethod
    def _invoice_values(invoice: InputInvoiceIn) -> dict[str, Any]:
        """照錄供應商原始發票上的號碼、日期與三個金額。"""
        return {
            "invoice_number": invoice.invoice_number,
            "invoice_date": invoice.invoice_date,
            "invoice_total": Decimal(invoice.invoice_total),
            "invoice_net": Decimal(invoice.invoice_net),
            "invoice_tax": Decimal(invoice.invoice_tax),
        }

    async def _matching_invoice(
        self, store_id: int, supplier_id: int, invoice: InputInvoiceIn
    ) -> InputInvoice | None:
        """同號同日已登錄：同供應商、同金額＝同一張（回傳它）；否則是重複入帳 → 擋。"""
        found = await self._repo.find_input_invoice(
            store_id, invoice.invoice_number, invoice.invoice_date
        )
        if found is None:
            return None
        values = self._invoice_values(invoice)
        same = found.supplier_id == supplier_id and all(
            getattr(found, key) == values[key]
            for key in ("invoice_total", "invoice_net", "invoice_tax")
        )
        if not same:
            raise DuplicateInputInvoice(
                f"發票 {invoice.invoice_number}（{invoice.invoice_date}）已登錄過"
                f"（{found.supplier_name}，含稅 {found.invoice_total}），不可重複入帳；"
                "金額填錯請到進項發票修改"
            )
        return found

    async def _claim_receipts(
        self,
        store_id: int,
        supplier_id: int,
        receipt_ids: list[int],
        *,
        invoice_id: int | None,
    ) -> list[GoodsReceipt]:
        """驗證並鎖住要掛上的收貨：都存在、都是這家供應商、沒掛在別張發票上。"""
        wanted = sorted(set(receipt_ids))
        receipts = await self._repo.receipts_for_update(store_id, wanted)
        if len(receipts) != len(wanted):
            raise InputInvoiceInvalid("有收貨批次找不到，請重新整理後再選")
        suppliers = await self._repo.suppliers_of_orders(
            store_id, [r.purchase_order_id for r in receipts]
        )
        for receipt in receipts:
            if suppliers[receipt.purchase_order_id] != supplier_id:
                order = await self._repo.get_purchase_order(store_id, receipt.purchase_order_id)
                name = order.supplier_name if order is not None else ""
                raise InputInvoiceInvalid(
                    f"採購單 #{receipt.purchase_order_id} 的收貨是「{name}」的，"
                    "不能放進別家供應商的發票"
                )
            if receipt.input_invoice_id not in (None, invoice_id):
                raise InputInvoiceAlreadySet(
                    f"採購單 #{receipt.purchase_order_id} 這批收貨已經登在別張發票上"
                )
        return receipts

    async def _invoice_supplier(self, store_id: int, supplier_id: int) -> Supplier:
        supplier = await self._repo.get_supplier(store_id, supplier_id)
        if supplier is None:
            raise CrossStoreReference(f"供應商 {supplier_id} 不屬於 store {store_id}")
        return supplier

    async def create_input_invoice(
        self, store_id: int, payload: InputInvoiceWrite, *, actor_user_id: int
    ) -> InputInvoice:
        """登錄一張進項發票並掛上它涵蓋的收貨（收貨後隔月才開也行）。"""
        supplier = await self._invoice_supplier(store_id, payload.supplier_id)
        receipts = await self._claim_receipts(
            store_id, supplier.id, payload.receipt_ids, invoice_id=None
        )
        if await self._repo.find_input_invoice(
            store_id, payload.invoice_number, payload.invoice_date
        ):
            raise DuplicateInputInvoice(
                f"發票 {payload.invoice_number}（{payload.invoice_date}）已登錄過，不可重複入帳"
            )
        invoice = await self._repo.add_input_invoice(
            InputInvoice(
                store_id=store_id,
                supplier_id=supplier.id,
                supplier_name=supplier.name,
                created_by=actor_user_id,
                **self._invoice_values(payload),
            )
        )
        for receipt in receipts:
            receipt.input_invoice_id = invoice.id
        await self._session.flush()
        return await self._reloaded_invoice(store_id, invoice.id)

    async def update_input_invoice(
        self,
        store_id: int,
        invoice_id: int,
        payload: InputInvoiceWrite,
        *,
        actor_user_id: int,
        actor_is_manager: bool,
    ) -> InputInvoice:
        """更正一張進項發票（號碼、日期、金額、涵蓋的收貨）；限管理者，前後值寫稽核。"""
        if not actor_is_manager:
            raise PurchasingManagerOnly("進項發票登錄後只有管理者能修改")
        invoice = await self._locked_invoice(store_id, invoice_id)
        before = self._invoice_snapshot(invoice)
        supplier = await self._invoice_supplier(store_id, payload.supplier_id)
        receipts = await self._claim_receipts(
            store_id, supplier.id, payload.receipt_ids, invoice_id=invoice.id
        )
        same_key = await self._repo.find_input_invoice(
            store_id, payload.invoice_number, payload.invoice_date
        )
        if same_key is not None and same_key.id != invoice.id:
            raise DuplicateInputInvoice(
                f"發票 {payload.invoice_number}（{payload.invoice_date}）已登錄過，不可重複入帳"
            )
        keep = {receipt.id for receipt in receipts}
        for receipt in list(invoice.receipts):
            if receipt.id not in keep:
                receipt.input_invoice_id = None
        for receipt in receipts:
            receipt.input_invoice_id = invoice.id
        if supplier.id != invoice.supplier_id:
            invoice.supplier_id = supplier.id
            invoice.supplier_name = supplier.name
        for key, value in self._invoice_values(payload).items():
            setattr(invoice, key, value)
        await self._session.flush()
        updated = await self._reloaded_invoice(store_id, invoice.id)
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="UPDATE_INPUT_INVOICE",
            entity_type="purchase_input_invoice",
            entity_id=str(invoice.id),
            before=before,
            after=self._invoice_snapshot(updated),
        )
        return updated

    async def delete_input_invoice(
        self, store_id: int, invoice_id: int, *, actor_user_id: int, actor_is_manager: bool
    ) -> None:
        """刪掉登錯的發票；它涵蓋的收貨回到「還沒開發票」。限管理者，寫稽核。"""
        if not actor_is_manager:
            raise PurchasingManagerOnly("進項發票登錄後只有管理者能刪除")
        invoice = await self._locked_invoice(store_id, invoice_id)
        before = self._invoice_snapshot(invoice)
        for receipt in list(invoice.receipts):
            receipt.input_invoice_id = None
        await self._session.flush()
        await self._session.delete(invoice)
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="DELETE_INPUT_INVOICE",
            entity_type="purchase_input_invoice",
            entity_id=str(invoice_id),
            before=before,
            after=None,
        )

    async def _locked_invoice(self, store_id: int, invoice_id: int) -> InputInvoice:
        invoice = await self._repo.get_input_invoice(store_id, invoice_id, for_update=True)
        if invoice is None:
            raise InputInvoiceNotFound(f"找不到進項發票 {invoice_id}")
        await self._session.refresh(invoice, ["receipts"])
        return invoice

    async def _reloaded_invoice(self, store_id: int, invoice_id: int) -> InputInvoice:
        invoice = await self._repo.get_input_invoice(store_id, invoice_id)
        assert invoice is not None
        await self._session.refresh(invoice, ["receipts"])
        return invoice

    @staticmethod
    def _invoice_snapshot(invoice: InputInvoice) -> dict[str, Any]:
        return {
            "supplier_id": invoice.supplier_id,
            "invoice_number": invoice.invoice_number,
            "invoice_date": invoice.invoice_date.isoformat(),
            "invoice_net": str(invoice.invoice_net),
            "invoice_tax": str(invoice.invoice_tax),
            "invoice_total": str(invoice.invoice_total),
            "receipt_ids": sorted(receipt.id for receipt in invoice.receipts),
        }

    async def get_input_invoice(self, store_id: int, invoice_id: int) -> InputInvoice | None:
        return await self._repo.get_input_invoice(store_id, invoice_id)

    async def list_input_invoices(
        self, store_id: int, *, supplier_id: int | None = None, limit: int = 50, offset: int = 0
    ) -> list[InputInvoice]:
        return await self._repo.list_input_invoices(
            store_id, supplier_id=supplier_id, limit=limit, offset=offset
        )

    async def count_input_invoices(self, store_id: int, *, supplier_id: int | None = None) -> int:
        return await self._repo.count_input_invoices(store_id, supplier_id=supplier_id)

    async def uninvoiced_receipts(self, store_id: int, supplier_id: int) -> list[GoodsReceipt]:
        """某供應商還沒開發票的收貨批次（登錄發票時勾選用）。"""
        return await self._repo.uninvoiced_receipts(store_id, supplier_id)

    async def receipt_amounts(
        self, store_id: int, receipts: list[GoodsReceipt]
    ) -> dict[int, Decimal]:
        """{收貨批次: 金額}＝這批各商品入庫數量 × 該採購單的進價（進項發票對帳提示用）。"""
        quantities = await self._inventory.purchase_in_by_receipt(
            store_id, [receipt.id for receipt in receipts]
        )
        costs = await self._repo.unit_costs_of_orders(
            store_id, sorted({receipt.purchase_order_id for receipt in receipts})
        )
        return {
            receipt.id: sum(
                (
                    Decimal(qty) * costs.get((receipt.purchase_order_id, product), Decimal(0))
                    for product, qty in quantities.get(receipt.id, {}).items()
                ),
                Decimal(0),
            )
            for receipt in receipts
        }

    @staticmethod
    def _receive_fingerprint(
        purchase_order_id: int,
        *,
        lines: list["ReceiveLineIn"],
        invoice: "InputInvoiceIn | None",
    ) -> str:
        """定義收貨業務請求的穩定指紋，供同鍵回放或衝突判定。"""
        canonical = {
            "purchase_order_id": purchase_order_id,
            "lines": sorted((line.line_id, line.qty) for line in lines),
            "invoice": (
                None
                if invoice is None
                else [
                    invoice.invoice_number,
                    invoice.invoice_date.isoformat(),
                    str(invoice.invoice_net),
                    str(invoice.invoice_tax),
                    str(invoice.invoice_total),
                ]
            ),
        }
        blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
