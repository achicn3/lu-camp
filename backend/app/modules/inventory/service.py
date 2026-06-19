"""inventory 業務邏輯：狀態機、ownership 驗證、散裝扣減、主檔 get-or-create、定價輔助。"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import suggested_price
from app.modules.inventory.models import (
    Brand,
    BulkLot,
    CatalogProduct,
    Category,
    CategoryPricingRule,
    ProductModel,
    SerializedItem,
    StockMovement,
)
from app.modules.inventory.pricing_defaults import (
    DEFAULT_DISCOUNT_CEILING_PCT,
    DEFAULT_MIN_MARGIN_PCT,
    DEFAULT_MIN_PRICE_MULTIPLE,
    PRICING_BANDS,
)
from app.modules.inventory.repository import InventoryRepository
from app.shared.enums import (
    BulkAcquisitionBasis,
    BulkLotStatus,
    Grade,
    ItemKind,
    OwnershipType,
    SerializedItemStatus,
    StockDirection,
    StockReason,
)
from app.shared.exceptions import (
    AcquisitionHasSoldItems,
    CrossStoreReference,
    InsufficientStock,
    InvalidStateTransition,
    OwnershipValidationError,
)


class InventoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = InventoryRepository(session)

    # ── 主檔 ──
    async def get_or_create_brand(self, store_id: int, name: str) -> Brand:
        return await self._repo.get_or_create_brand(store_id, name)

    async def get_or_create_product_model(
        self, store_id: int, brand_id: int, name: str
    ) -> ProductModel:
        if await self._repo.get_brand(store_id, brand_id) is None:
            raise CrossStoreReference(f"品牌 {brand_id} 不屬於 store {store_id}")
        return await self._repo.get_or_create_product_model(store_id, brand_id, name)

    async def list_brands(
        self, store_id: int, *, q: str | None = None, limit: int = 50
    ) -> list[Brand]:
        return await self._repo.list_brands(store_id, q=q, limit=limit)

    async def get_brand(self, store_id: int, brand_id: int) -> Brand | None:
        return await self._repo.get_brand(store_id, brand_id)

    async def get_product_model(self, store_id: int, product_model_id: int) -> ProductModel | None:
        return await self._repo.get_product_model(store_id, product_model_id)

    async def list_product_models(
        self,
        store_id: int,
        *,
        brand_id: int | None = None,
        q: str | None = None,
        limit: int = 50,
    ) -> list[ProductModel]:
        return await self._repo.list_product_models(
            store_id, brand_id=brand_id, q=q, limit=limit
        )

    # ── 分類 / 定價規則 ──
    async def list_categories(
        self, store_id: int, *, q: str | None = None, limit: int = 50
    ) -> list[Category]:
        return await self._repo.list_categories(store_id, q=q, limit=limit)

    async def get_category(self, store_id: int, category_id: int) -> Category | None:
        return await self._repo.get_category(store_id, category_id)

    async def get_or_create_category(
        self, store_id: int, name: str, *, default_target_margin_pct: int
    ) -> Category:
        """查無即建分類；建立時 seed 各成色帶 v1 定價規則（同名回既有，冪等）。"""
        existing = await self._repo.get_category_by_name(store_id, name)
        if existing is not None:
            return existing
        category = await self._repo.add_category(
            Category(store_id=store_id, name=name, target_margin_pct=default_target_margin_pct)
        )
        for band in PRICING_BANDS:
            await self._repo.add_pricing_rule(
                CategoryPricingRule(
                    store_id=store_id,
                    category_id=category.id,
                    condition_band=band,
                    discount_ceiling_pct=DEFAULT_DISCOUNT_CEILING_PCT,
                    min_margin_pct=DEFAULT_MIN_MARGIN_PCT,
                    min_price_multiple=DEFAULT_MIN_PRICE_MULTIPLE,
                )
            )
        return category

    async def update_category_target(
        self, store_id: int, category_id: int, target_margin_pct: int
    ) -> Category | None:
        """更新分類目標毛利率（manager；router 驗權）。不存在回 None。"""
        category = await self._repo.get_category(store_id, category_id)
        if category is None:
            return None
        category.target_margin_pct = target_margin_pct
        await self._session.flush()
        return category

    async def list_pricing_rules(
        self, store_id: int, category_id: int
    ) -> list[CategoryPricingRule]:
        return await self._repo.list_pricing_rules(store_id, category_id)

    async def update_pricing_rules(
        self,
        store_id: int,
        category_id: int,
        updates: list[tuple[Grade, int, int, Decimal]],
    ) -> list[CategoryPricingRule] | None:
        """批次更新該分類各成色帶規則（manager）。分類不存在回 None；未知成色帶略過。

        updates：(condition_band, discount_ceiling_pct, min_margin_pct, min_price_multiple)。
        """
        if await self._repo.get_category(store_id, category_id) is None:
            return None
        for band, ceiling, margin, multiple in updates:
            rule = await self._repo.get_pricing_rule(store_id, category_id, band)
            if rule is None:
                continue
            rule.discount_ceiling_pct = ceiling
            rule.min_margin_pct = margin
            rule.min_price_multiple = multiple
        await self._session.flush()
        return await self._repo.list_pricing_rules(store_id, category_id)

    # ── 序號單品 ──
    async def create_serialized_item(
        self,
        store_id: int,
        *,
        item_code: str,
        name: str,
        grade: Grade,
        ownership_type: OwnershipType,
        listed_price: Decimal,
        brand_id: int | None = None,
        product_model_id: int | None = None,
        acquisition_cost: Decimal | None = None,
        consignor_id: int | None = None,
        commission_pct: int | None = None,
        acquisition_id: int | None = None,
        category_id: int | None = None,
    ) -> SerializedItem:
        if grade == Grade.E:
            raise OwnershipValidationError("E 級為散裝批，不走序號單品")
        if ownership_type == OwnershipType.OWNED:
            if acquisition_cost is None:
                raise OwnershipValidationError("OWNED 必須有 acquisition_cost")
        elif consignor_id is None or commission_pct is None:
            raise OwnershipValidationError("CONSIGNMENT 必須有 consignor_id 與 commission_pct")

        await self._validate_item_references(
            store_id,
            brand_id=brand_id,
            product_model_id=product_model_id,
            category_id=category_id,
        )

        item = SerializedItem(
            store_id=store_id,
            item_code=item_code,
            name=name,
            grade=grade,
            ownership_type=ownership_type,
            listed_price=listed_price,
            brand_id=brand_id,
            product_model_id=product_model_id,
            acquisition_cost=acquisition_cost,
            consignor_id=consignor_id,
            commission_pct=commission_pct,
            acquisition_id=acquisition_id,
            category_id=category_id,
        )
        return await self._repo.add_serialized(item)

    async def _validate_item_references(
        self,
        store_id: int,
        *,
        brand_id: int | None,
        product_model_id: int | None = None,
        category_id: int | None = None,
    ) -> None:
        """Validate optional inventory references are owned by this store before insert."""
        if brand_id is not None and await self._repo.get_brand(store_id, brand_id) is None:
            raise CrossStoreReference(f"品牌 {brand_id} 不屬於 store {store_id}")
        if product_model_id is not None:
            model = await self._repo.get_product_model(store_id, product_model_id)
            if model is None:
                raise CrossStoreReference(f"型號 {product_model_id} 不屬於 store {store_id}")
            if brand_id is not None and model.brand_id != brand_id:
                raise CrossStoreReference(f"型號 {product_model_id} 不屬於品牌 {brand_id}")
        if category_id is not None and await self._repo.get_category(store_id, category_id) is None:
            raise CrossStoreReference(f"分類 {category_id} 不屬於 store {store_id}")

    # ── 庫存異動帳 ──
    async def record_stock_in(
        self,
        store_id: int,
        item_kind: ItemKind,
        *,
        qty: int,
        reason: StockReason,
        ref_type: str | None = None,
        ref_id: int | None = None,
        serialized_item_id: int | None = None,
        catalog_product_id: int | None = None,
        bulk_lot_id: int | None = None,
    ) -> StockMovement:
        """記一筆入庫（IN）異動帳；供收購/進貨等流程在同一交易內呼叫。"""
        movement = StockMovement(
            store_id=store_id,
            item_kind=item_kind,
            direction=StockDirection.IN,
            qty=qty,
            reason=reason,
            ref_type=ref_type,
            ref_id=ref_id,
            serialized_item_id=serialized_item_id,
            catalog_product_id=catalog_product_id,
            bulk_lot_id=bulk_lot_id,
        )
        return await self._repo.add_stock_movement(movement)

    async def record_stock_out(
        self,
        store_id: int,
        item_kind: ItemKind,
        *,
        qty: int,
        reason: StockReason,
        ref_type: str | None = None,
        ref_id: int | None = None,
        serialized_item_id: int | None = None,
        catalog_product_id: int | None = None,
        bulk_lot_id: int | None = None,
    ) -> StockMovement:
        """記一筆出庫（OUT）異動帳；供銷售/退貨等流程在同一交易內呼叫。"""
        movement = StockMovement(
            store_id=store_id,
            item_kind=item_kind,
            direction=StockDirection.OUT,
            qty=qty,
            reason=reason,
            ref_type=ref_type,
            ref_id=ref_id,
            serialized_item_id=serialized_item_id,
            catalog_product_id=catalog_product_id,
            bulk_lot_id=bulk_lot_id,
        )
        return await self._repo.add_stock_movement(movement)

    async def _transition(self, item_id: int, to_status: SerializedItemStatus) -> None:
        """合法轉移一律自 IN_STOCK 出發（由條件式 UPDATE 原子強制，亦擋併發重複）。"""
        ok = await self._repo.transition_serialized_status(
            item_id,
            SerializedItemStatus.IN_STOCK,
            to_status,
            set_sold_date=to_status == SerializedItemStatus.SOLD,
        )
        if not ok:
            raise InvalidStateTransition(
                f"序號品非 IN_STOCK，無法轉移到 {to_status}（如已售出/已下架）"
            )

    async def get_serialized_by_code(self, store_id: int, item_code: str) -> SerializedItem | None:
        """以 item_code 取序號品（供 POS 掃碼查件、讀取售價/ownership）。"""
        return await self._repo.get_serialized_by_code(store_id, item_code)

    async def list_serialized(
        self,
        store_id: int,
        *,
        status: SerializedItemStatus | None = None,
        ownership_type: OwnershipType | None = None,
        consignor_id: int | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SerializedItem]:
        """列序號品（庫存頁/POS 查件；篩 status/ownership/consignor、q 搜品名碼；§4 店別範圍）。"""
        return await self._repo.list_serialized(
            store_id,
            status=status,
            ownership_type=ownership_type,
            consignor_id=consignor_id,
            q=q,
            limit=limit,
            offset=offset,
        )

    async def list_catalog(
        self,
        store_id: int,
        *,
        q: str | None = None,
        low_stock: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CatalogProduct]:
        """列數量型商品（POS 選件/庫存頁；q 搜品名/SKU、low_stock 篩 量≤再訂購點）。"""
        return await self._repo.list_catalog(
            store_id, q=q, low_stock=low_stock, limit=limit, offset=offset
        )

    async def list_bulk_lots(
        self,
        store_id: int,
        *,
        status: BulkLotStatus | None = None,
        consignor_id: int | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BulkLot]:
        """列散裝堆（POS 明確選堆/庫存頁；篩狀態/consignor、q 搜名稱/堆名/識別碼）。"""
        return await self._repo.list_bulk_lots(
            store_id, status=status, consignor_id=consignor_id, q=q, limit=limit, offset=offset
        )

    async def list_serialized_by_acquisitions(
        self,
        store_id: int,
        acquisition_ids: list[int],
        *,
        status: SerializedItemStatus | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[SerializedItem]:
        """指定收購單下的序號品（會員中心買斷來源；可選 status；空 ids → []；docs/17 §5.2）。"""
        return await self._repo.list_serialized_by_acquisitions(
            store_id, acquisition_ids, status=status, limit=limit, offset=offset
        )

    async def list_bulk_lots_by_acquisitions(
        self,
        store_id: int,
        acquisition_ids: list[int],
        *,
        status: BulkLotStatus | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[BulkLot]:
        """指定收購單下的散裝堆（會員中心買斷來源；可選 status；空 ids → []；§5.2）。"""
        return await self._repo.list_bulk_lots_by_acquisitions(
            store_id, acquisition_ids, status=status, limit=limit, offset=offset
        )

    async def list_serialized_ids_by_consignor(
        self, store_id: int, consignor_id: int
    ) -> list[int]:
        """某寄售人的寄售序號品 id（供結算 PENDING 應撥聚合；id-only；docs/17 §3.4）。"""
        return await self._repo.list_serialized_ids_by_consignor(store_id, consignor_id)

    async def count_serialized_by_consignor(self, store_id: int, consignor_id: int) -> int:
        return await self._repo.count_serialized_by_consignor(store_id, consignor_id)

    async def count_bulk_lots_by_consignor(self, store_id: int, consignor_id: int) -> int:
        return await self._repo.count_bulk_lots_by_consignor(store_id, consignor_id)

    async def sell_serialized_item(self, item_id: int) -> None:
        await self._transition(item_id, SerializedItemStatus.SOLD)

    async def return_serialized_to_consignor(self, item_id: int) -> None:
        await self._transition(item_id, SerializedItemStatus.RETURNED_TO_CONSIGNOR)

    async def write_off_serialized_item(self, item_id: int) -> None:
        await self._transition(item_id, SerializedItemStatus.WRITTEN_OFF)

    async def prelock_serialized_for_sale(self, store_id: int, item_codes: list[str]) -> None:
        """銷售前置：依 id 升冪鎖定本單所有序號品列，建立與收購作廢一致的全域鎖序（防 AB-BA）。

        解析→排序→逐列 FOR UPDATE；之後逐行（購物車序）的 sell 只是再觸碰已持有的鎖，不另以
        購物車序取鎖。與作廢的 id 序退場一致，避免多件、購物車反序的銷售與作廢互卡死結。
        """
        ids = await self._repo.list_serialized_ids_by_codes(store_id, item_codes)
        for item_id in ids:  # list_serialized_ids_by_codes 已升冪
            await self._repo.lock_serialized_row(store_id, item_id)

    async def has_sold_items(self, store_id: int, acquisition_id: int) -> bool:
        """該收購入庫的庫存是否已有任一售出/動用（read-only，作廢前置擋下用，F6.5）。

        序號品：任一非 IN_STOCK（已售/已退寄售人/已下架）即視為動用；
        散裝批：任一非 ON_SALE 或 remaining_qty < total_qty（已部分/全部售出）即視為動用。
        以無分頁讀層涵蓋整批（不可漏看 201+ 件之後的頁，Codex 高風險）。
        """
        items = await self._repo.list_owned_serialized_for_void(store_id, acquisition_id)
        if any(it.status != SerializedItemStatus.IN_STOCK for it in items):
            return True
        lots = await self._repo.list_owned_bulk_lots_for_void(store_id, acquisition_id)
        return any(
            lot.status != BulkLotStatus.ON_SALE or lot.remaining_qty != lot.total_qty
            for lot in lots
        )

    async def void_acquisition_inventory(self, store_id: int, acquisition_id: int) -> None:
        """作廢收購：將該收購入庫的序號品/散裝批全部退場（WRITTEN_OFF＋出庫帳）。

        以原子條件式轉移為併發後盾——任一品在前置檢查後才被售出，轉移失敗即丟
        AcquisitionHasSoldItems、整筆回滾，不留半套。stock_movement 以 ref 溯源到本作廢。
        以無分頁讀層涵蓋整批（不可漏退 201+ 件之後的頁，Codex 高風險）。
        """
        items = await self._repo.list_owned_serialized_for_void(store_id, acquisition_id)
        for it in items:
            ok = await self._repo.transition_serialized_status(
                it.id,
                SerializedItemStatus.IN_STOCK,
                SerializedItemStatus.WRITTEN_OFF,
                set_sold_date=False,
            )
            if not ok:
                raise AcquisitionHasSoldItems(
                    f"序號品 {it.item_code} 已售出/已下架，無法作廢收購"
                )
            await self.record_stock_out(
                store_id,
                ItemKind.SERIALIZED,
                qty=1,
                reason=StockReason.WRITE_OFF,
                ref_type="acquisition_void",
                ref_id=acquisition_id,
                serialized_item_id=it.id,
            )
        lots = await self._repo.list_owned_bulk_lots_for_void(store_id, acquisition_id)
        for lot in lots:
            if not await self._repo.write_off_bulk_lot(lot.id):
                raise AcquisitionHasSoldItems(
                    f"散裝批 {lot.lot_code} 已部分/全部售出，無法作廢收購"
                )
            await self.record_stock_out(
                store_id,
                ItemKind.BULK_LOT,
                qty=lot.total_qty,
                reason=StockReason.WRITE_OFF,
                ref_type="acquisition_void",
                ref_id=acquisition_id,
                bulk_lot_id=lot.id,
            )

    # ── 散裝批 ──
    async def create_bulk_lot(
        self,
        store_id: int,
        *,
        lot_code: str,
        name: str,
        grade: Grade,
        acquisition_cost: Decimal,
        acquisition_basis: BulkAcquisitionBasis,
        unit_price: Decimal,
        total_qty: int,
        brand_id: int | None = None,
        consignor_id: int | None = None,
        label: str | None = None,
        acquisition_id: int | None = None,
        category_id: int | None = None,
    ) -> BulkLot:
        if grade != Grade.E:
            raise OwnershipValidationError("散裝批 grade 必須為 E")
        if total_qty <= 0:
            raise OwnershipValidationError("散裝批 total_qty 必須 > 0")

        await self._validate_item_references(store_id, brand_id=brand_id, category_id=category_id)

        lot = BulkLot(
            store_id=store_id,
            lot_code=lot_code,
            name=name,
            grade=grade,
            acquisition_cost=acquisition_cost,
            acquisition_basis=acquisition_basis,
            unit_price=unit_price,
            total_qty=total_qty,
            remaining_qty=total_qty,
            brand_id=brand_id,
            consignor_id=consignor_id,
            label=label,
            acquisition_id=acquisition_id,
            category_id=category_id,
        )
        return await self._repo.add_bulk_lot(lot)

    async def get_bulk_lot_by_code(self, store_id: int, lot_code: str) -> BulkLot | None:
        """以 lot_code 取散裝堆（POS 掃堆標籤；docs/04）。"""
        return await self._repo.get_bulk_lot_by_code(store_id, lot_code)

    async def get_bulk_lot(self, store_id: int, lot_id: int) -> BulkLot | None:
        return await self._repo.get_bulk_lot(store_id, lot_id)

    async def sell_bulk_lot_items(self, lot_id: int, qty: int) -> None:
        if qty <= 0:
            raise InsufficientStock("售出數量必須 > 0")
        ok = await self._repo.decrement_bulk_lot(lot_id, qty)
        if not ok:
            raise InsufficientStock("散裝批庫存不足，無法售出")

    # ── 數量型商品 ──
    async def get_catalog(self, store_id: int, catalog_id: int) -> CatalogProduct | None:
        return await self._repo.get_catalog(store_id, catalog_id)

    async def sell_catalog_items(self, catalog_id: int, qty: int) -> None:
        """原子扣減數量型商品庫存；不足則拒絕（不先查再改，併發安全）。"""
        if qty <= 0:
            raise InsufficientStock("售出數量必須 > 0")
        ok = await self._repo.decrement_catalog(catalog_id, qty)
        if not ok:
            raise InsufficientStock("數量型商品庫存不足，無法售出")

    async def restock_catalog_items(
        self,
        store_id: int,
        catalog_id: int,
        qty: int,
        *,
        ref_type: str,
        ref_id: int,
    ) -> None:
        """數量型商品補貨入庫：加庫存並寫 PURCHASE stock_movement（同一交易）。"""
        if qty <= 0:
            raise OwnershipValidationError("入庫數量必須 > 0")
        ok = await self._repo.increment_catalog(store_id, catalog_id, qty)
        if not ok:
            raise CrossStoreReference(f"數量型商品 {catalog_id} 不屬於 store {store_id}")
        await self.record_stock_in(
            store_id,
            ItemKind.CATALOG,
            qty=qty,
            reason=StockReason.PURCHASE,
            ref_type=ref_type,
            ref_id=ref_id,
            catalog_product_id=catalog_id,
        )

    @staticmethod
    def per_piece_cost(lot: BulkLot) -> Decimal:
        """每件成本 = acquisition_cost / total_qty。"""
        return lot.acquisition_cost / Decimal(lot.total_qty)

    @staticmethod
    def suggested_listed_price(acquisition_cost: Decimal, margin_pct: int) -> int:
        """收購定價輔助（含稅整數元）；委派 core/money。"""
        return suggested_price(acquisition_cost, margin_pct)
