"""inventory 業務邏輯：狀態機、ownership 驗證、散裝扣減、主檔 get-or-create、定價輔助。"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import suggested_price
from app.modules.inventory.models import (
    Brand,
    BulkLot,
    ProductModel,
    SerializedItem,
    StockMovement,
)
from app.modules.inventory.repository import InventoryRepository
from app.shared.enums import (
    BulkAcquisitionBasis,
    Grade,
    ItemKind,
    OwnershipType,
    SerializedItemStatus,
    StockDirection,
    StockReason,
)
from app.shared.exceptions import (
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
        return await self._repo.get_or_create_product_model(store_id, brand_id, name)

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
    ) -> SerializedItem:
        if grade == Grade.E:
            raise OwnershipValidationError("E 級為散裝批，不走序號單品")
        if ownership_type == OwnershipType.OWNED:
            if acquisition_cost is None:
                raise OwnershipValidationError("OWNED 必須有 acquisition_cost")
        elif consignor_id is None or commission_pct is None:
            raise OwnershipValidationError("CONSIGNMENT 必須有 consignor_id 與 commission_pct")

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
        )
        return await self._repo.add_serialized(item)

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

    async def sell_serialized_item(self, item_id: int) -> None:
        await self._transition(item_id, SerializedItemStatus.SOLD)

    async def return_serialized_to_consignor(self, item_id: int) -> None:
        await self._transition(item_id, SerializedItemStatus.RETURNED_TO_CONSIGNOR)

    async def write_off_serialized_item(self, item_id: int) -> None:
        await self._transition(item_id, SerializedItemStatus.WRITTEN_OFF)

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
    ) -> BulkLot:
        if grade != Grade.E:
            raise OwnershipValidationError("散裝批 grade 必須為 E")
        if total_qty <= 0:
            raise OwnershipValidationError("散裝批 total_qty 必須 > 0")

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
        )
        return await self._repo.add_bulk_lot(lot)

    async def sell_bulk_lot_items(self, lot_id: int, qty: int) -> None:
        if qty <= 0:
            raise InsufficientStock("售出數量必須 > 0")
        ok = await self._repo.decrement_bulk_lot(lot_id, qty)
        if not ok:
            raise InsufficientStock("散裝批庫存不足，無法售出")

    @staticmethod
    def per_piece_cost(lot: BulkLot) -> Decimal:
        """每件成本 = acquisition_cost / total_qty。"""
        return lot.acquisition_cost / Decimal(lot.total_qty)

    @staticmethod
    def suggested_listed_price(acquisition_cost: Decimal, margin_pct: int) -> int:
        """收購定價輔助（含稅整數元）；委派 core/money。"""
        return suggested_price(acquisition_cost, margin_pct)
