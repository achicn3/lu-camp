"""inventory 資料存取層（唯一直接碰 ORM 的層）。

狀態轉移與散裝扣減以「條件式 UPDATE + rowcount」達成原子性，
使併發下同一序號品只成功一筆、散裝批不會超賣。
"""

from typing import Any, cast

from sqlalchemy import CursorResult, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.shared.enums import BulkLotStatus, Grade, OwnershipType, SerializedItemStatus


class InventoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── 主檔 ──
    async def get_or_create_brand(self, store_id: int, name: str) -> Brand:
        stmt = select(Brand).where(Brand.store_id == store_id, Brand.name == name)
        brand: Brand | None = await self._session.scalar(stmt)
        if brand is None:
            brand = Brand(store_id=store_id, name=name)
            self._session.add(brand)
            await self._session.flush()
        return brand

    async def get_or_create_product_model(
        self, store_id: int, brand_id: int, name: str
    ) -> ProductModel:
        # 以 (store, brand, name) 去重：同名型號可分屬不同品牌（autocomplete 建立時按品牌歸屬）。
        stmt = select(ProductModel).where(
            ProductModel.store_id == store_id,
            ProductModel.brand_id == brand_id,
            ProductModel.name == name,
        )
        model: ProductModel | None = await self._session.scalar(stmt)
        if model is None:
            model = ProductModel(store_id=store_id, brand_id=brand_id, name=name)
            self._session.add(model)
            await self._session.flush()
        return model

    async def list_brands(self, store_id: int, *, q: str | None, limit: int) -> list[Brand]:
        stmt = select(Brand).where(Brand.store_id == store_id)
        if q:
            stmt = stmt.where(Brand.name.ilike(f"%{q}%"))
        stmt = stmt.order_by(Brand.name).limit(limit)
        return list((await self._session.scalars(stmt)).all())

    async def get_brand(self, store_id: int, brand_id: int) -> Brand | None:
        stmt = select(Brand).where(Brand.id == brand_id, Brand.store_id == store_id)
        result: Brand | None = await self._session.scalar(stmt)
        return result

    async def get_product_model(self, store_id: int, product_model_id: int) -> ProductModel | None:
        stmt = select(ProductModel).where(
            ProductModel.id == product_model_id, ProductModel.store_id == store_id
        )
        result: ProductModel | None = await self._session.scalar(stmt)
        return result

    async def list_product_models(
        self, store_id: int, *, brand_id: int | None, q: str | None, limit: int
    ) -> list[ProductModel]:
        stmt = select(ProductModel).where(ProductModel.store_id == store_id)
        if brand_id is not None:
            stmt = stmt.where(ProductModel.brand_id == brand_id)
        if q:
            stmt = stmt.where(ProductModel.name.ilike(f"%{q}%"))
        stmt = stmt.order_by(ProductModel.name).limit(limit)
        return list((await self._session.scalars(stmt)).all())

    # ── 分類 / 定價規則 ──
    async def get_category_by_name(self, store_id: int, name: str) -> Category | None:
        stmt = select(Category).where(Category.store_id == store_id, Category.name == name)
        result: Category | None = await self._session.scalar(stmt)
        return result

    async def get_category(self, store_id: int, category_id: int) -> Category | None:
        stmt = select(Category).where(Category.id == category_id, Category.store_id == store_id)
        result: Category | None = await self._session.scalar(stmt)
        return result

    async def add_category(self, category: Category) -> Category:
        self._session.add(category)
        await self._session.flush()
        return category

    async def list_categories(
        self, store_id: int, *, q: str | None, limit: int
    ) -> list[Category]:
        stmt = select(Category).where(Category.store_id == store_id)
        if q:
            stmt = stmt.where(Category.name.ilike(f"%{q}%"))
        stmt = stmt.order_by(Category.name).limit(limit)
        return list((await self._session.scalars(stmt)).all())

    async def add_pricing_rule(self, rule: CategoryPricingRule) -> CategoryPricingRule:
        self._session.add(rule)
        await self._session.flush()
        return rule

    async def list_pricing_rules(
        self, store_id: int, category_id: int
    ) -> list[CategoryPricingRule]:
        stmt = (
            select(CategoryPricingRule)
            .where(
                CategoryPricingRule.store_id == store_id,
                CategoryPricingRule.category_id == category_id,
            )
            .order_by(CategoryPricingRule.condition_band)
        )
        return list((await self._session.scalars(stmt)).all())

    async def get_pricing_rule(
        self, store_id: int, category_id: int, condition_band: Grade
    ) -> CategoryPricingRule | None:
        stmt = select(CategoryPricingRule).where(
            CategoryPricingRule.store_id == store_id,
            CategoryPricingRule.category_id == category_id,
            CategoryPricingRule.condition_band == condition_band,
        )
        result: CategoryPricingRule | None = await self._session.scalar(stmt)
        return result

    # ── 序號單品 ──
    async def add_serialized(self, item: SerializedItem) -> SerializedItem:
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_serialized_by_code(self, store_id: int, item_code: str) -> SerializedItem | None:
        stmt = select(SerializedItem).where(
            SerializedItem.store_id == store_id, SerializedItem.item_code == item_code
        )
        result: SerializedItem | None = await self._session.scalar(stmt)
        return result

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
        stmt = select(SerializedItem).where(SerializedItem.store_id == store_id)
        if status is not None:
            stmt = stmt.where(SerializedItem.status == status)
        if ownership_type is not None:
            stmt = stmt.where(SerializedItem.ownership_type == ownership_type)
        if consignor_id is not None:
            stmt = stmt.where(SerializedItem.consignor_id == consignor_id)
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                SerializedItem.name.ilike(pattern) | SerializedItem.item_code.ilike(pattern)
            )
        stmt = stmt.order_by(SerializedItem.id.desc()).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    async def list_catalog(
        self,
        store_id: int,
        *,
        q: str | None = None,
        low_stock: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CatalogProduct]:
        stmt = select(CatalogProduct).where(CatalogProduct.store_id == store_id)
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                CatalogProduct.name.ilike(pattern) | CatalogProduct.sku.ilike(pattern)
            )
        if low_stock:
            stmt = stmt.where(CatalogProduct.quantity_on_hand <= CatalogProduct.reorder_point)
        stmt = stmt.order_by(CatalogProduct.name).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

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
        stmt = select(BulkLot).where(BulkLot.store_id == store_id)
        if status is not None:
            stmt = stmt.where(BulkLot.status == status)
        if consignor_id is not None:
            stmt = stmt.where(BulkLot.consignor_id == consignor_id)
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                BulkLot.name.ilike(pattern)
                | BulkLot.lot_code.ilike(pattern)
                | BulkLot.label.ilike(pattern)
            )
        stmt = stmt.order_by(BulkLot.id.desc()).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    async def list_serialized_by_acquisitions(
        self,
        store_id: int,
        acquisition_ids: list[int],
        *,
        status: SerializedItemStatus | None = None,
        limit: int,
        offset: int,
    ) -> list[SerializedItem]:
        """指定收購單下的**買斷**序號品（空 ids → 空清單）。

        僅回 OWNED：收購單可能是 BUYOUT 或 CONSIGNMENT，後者的寄售品另經 consignor 路徑
        呈現；不過濾會使寄售品在「買斷來源」與「寄售」兩路重複/誤分類（Codex review P2）。
        status 於 DB 層過濾，確保分頁正確（過濾在 LIMIT 之前）。
        """
        if not acquisition_ids:
            return []
        stmt = select(SerializedItem).where(
            SerializedItem.store_id == store_id,
            SerializedItem.acquisition_id.in_(acquisition_ids),
            SerializedItem.ownership_type == OwnershipType.OWNED,
        )
        if status is not None:
            stmt = stmt.where(SerializedItem.status == status)
        stmt = stmt.order_by(SerializedItem.id.desc()).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    async def list_bulk_lots_by_acquisitions(
        self,
        store_id: int,
        acquisition_ids: list[int],
        *,
        status: BulkLotStatus | None = None,
        limit: int,
        offset: int,
    ) -> list[BulkLot]:
        """指定收購單下的**買斷/自有**散裝堆（空 ids → 空清單）。

        僅回 consignor_id IS NULL（店家自有）：寄售散裝另經 consignor 路徑呈現，
        避免重複/誤分類（Codex review P2）。status 於 DB 層過濾（分頁正確）。
        """
        if not acquisition_ids:
            return []
        stmt = select(BulkLot).where(
            BulkLot.store_id == store_id,
            BulkLot.acquisition_id.in_(acquisition_ids),
            BulkLot.consignor_id.is_(None),
        )
        if status is not None:
            stmt = stmt.where(BulkLot.status == status)
        stmt = stmt.order_by(BulkLot.id.desc()).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    async def list_serialized_ids_by_consignor(
        self, store_id: int, consignor_id: int
    ) -> list[int]:
        """某寄售人的所有寄售序號品 id（id-only，供結算 PENDING 應撥聚合；不載全列）。"""
        stmt = select(SerializedItem.id).where(
            SerializedItem.store_id == store_id,
            SerializedItem.consignor_id == consignor_id,
        )
        return list((await self._session.scalars(stmt)).all())

    async def count_serialized_by_consignor(self, store_id: int, consignor_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(SerializedItem)
            .where(
                SerializedItem.store_id == store_id,
                SerializedItem.consignor_id == consignor_id,
            )
        )
        return int(await self._session.scalar(stmt) or 0)

    async def count_bulk_lots_by_consignor(self, store_id: int, consignor_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(BulkLot)
            .where(BulkLot.store_id == store_id, BulkLot.consignor_id == consignor_id)
        )
        return int(await self._session.scalar(stmt) or 0)

    # ── 數量型商品 ──
    async def get_catalog(self, store_id: int, catalog_id: int) -> CatalogProduct | None:
        stmt = select(CatalogProduct).where(
            CatalogProduct.id == catalog_id, CatalogProduct.store_id == store_id
        )
        result: CatalogProduct | None = await self._session.scalar(stmt)
        return result

    async def decrement_catalog(self, catalog_id: int, qty: int) -> bool:
        """原子扣減 quantity_on_hand；不足則不動作。回傳是否成功一筆。"""
        stmt = (
            update(CatalogProduct)
            .where(CatalogProduct.id == catalog_id, CatalogProduct.quantity_on_hand >= qty)
            .values(quantity_on_hand=CatalogProduct.quantity_on_hand - qty)
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def transition_serialized_status(
        self,
        item_id: int,
        from_status: SerializedItemStatus,
        to_status: SerializedItemStatus,
        *,
        set_sold_date: bool,
    ) -> bool:
        """條件式狀態轉移；僅當目前為 from_status 才成功（回傳是否成功一筆）。"""
        base = update(SerializedItem).where(
            SerializedItem.id == item_id, SerializedItem.status == from_status
        )
        stmt = (
            base.values(status=to_status, sold_date=func.now())
            if set_sold_date
            else base.values(status=to_status)
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    # ── 庫存異動帳 ──
    async def add_stock_movement(self, movement: StockMovement) -> StockMovement:
        self._session.add(movement)
        await self._session.flush()
        return movement

    # ── 散裝批 ──
    async def add_bulk_lot(self, lot: BulkLot) -> BulkLot:
        self._session.add(lot)
        await self._session.flush()
        return lot

    async def get_bulk_lot(self, store_id: int, lot_id: int) -> BulkLot | None:
        stmt = select(BulkLot).where(BulkLot.id == lot_id, BulkLot.store_id == store_id)
        result: BulkLot | None = await self._session.scalar(stmt)
        return result

    async def get_bulk_lot_by_code(self, store_id: int, lot_code: str) -> BulkLot | None:
        stmt = select(BulkLot).where(BulkLot.lot_code == lot_code, BulkLot.store_id == store_id)
        result: BulkLot | None = await self._session.scalar(stmt)
        return result

    async def decrement_bulk_lot(self, lot_id: int, qty: int) -> bool:
        """原子扣減 remaining_qty；不足則不動作。歸零自動轉 SOLD_OUT。回傳是否成功。"""
        new_remaining = BulkLot.remaining_qty - qty
        stmt = (
            update(BulkLot)
            .where(BulkLot.id == lot_id, BulkLot.remaining_qty >= qty)
            .values(
                remaining_qty=new_remaining,
                status=case(
                    (new_remaining == 0, BulkLotStatus.SOLD_OUT),
                    else_=BulkLot.status,
                ),
            )
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1

    async def write_off_bulk_lot(self, lot_id: int) -> bool:
        """作廢收購時退場散裝批：原子地僅當「ON_SALE 且全未售（remaining = total）」才轉
        WRITTEN_OFF、remaining 歸 0。已部分/全部售出（或已下架）→ 不動作回 False（擋作廢）。"""
        stmt = (
            update(BulkLot)
            .where(
                BulkLot.id == lot_id,
                BulkLot.status == BulkLotStatus.ON_SALE,
                BulkLot.remaining_qty == BulkLot.total_qty,
            )
            .values(status=BulkLotStatus.WRITTEN_OFF, remaining_qty=0)
        )
        result = cast("CursorResult[Any]", await self._session.execute(stmt))
        return result.rowcount == 1
