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
    ProductModel,
    SerializedItem,
    StockMovement,
)
from app.shared.enums import BulkLotStatus, OwnershipType, SerializedItemStatus


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
        stmt = select(ProductModel).where(
            ProductModel.store_id == store_id, ProductModel.name == name
        )
        model: ProductModel | None = await self._session.scalar(stmt)
        if model is None:
            model = ProductModel(store_id=store_id, brand_id=brand_id, name=name)
            self._session.add(model)
            await self._session.flush()
        return model

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
        limit: int = 50,
        offset: int = 0,
    ) -> list[SerializedItem]:
        stmt = select(SerializedItem).where(SerializedItem.store_id == store_id)
        if status is not None:
            stmt = stmt.where(SerializedItem.status == status)
        if ownership_type is not None:
            stmt = stmt.where(SerializedItem.ownership_type == ownership_type)
        stmt = stmt.order_by(SerializedItem.id.desc()).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    async def list_catalog(
        self, store_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[CatalogProduct]:
        stmt = (
            select(CatalogProduct)
            .where(CatalogProduct.store_id == store_id)
            .order_by(CatalogProduct.name)
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.scalars(stmt)).all())

    async def list_bulk_lots(
        self,
        store_id: int,
        *,
        status: BulkLotStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BulkLot]:
        stmt = select(BulkLot).where(BulkLot.store_id == store_id)
        if status is not None:
            stmt = stmt.where(BulkLot.status == status)
        stmt = stmt.order_by(BulkLot.id.desc()).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

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
