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
    ProductModel,
    SerializedItem,
    StockMovement,
)
from app.shared.enums import BulkLotStatus, SerializedItemStatus


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
