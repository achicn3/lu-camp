"""stocktake 業務邏輯：建盤點單（快照 system_qty）→ 確認（依實點即時校正現量）。

第一版只盤一般商品（catalog_products）。確認時透過 inventory service 以 FOR UPDATE 重讀
現量計差額並寫 ADJUST(STOCKTAKE) 帳，避免清掉盤點期間的銷售。確認僅一次（狀態守衛）。
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.inventory.service import InventoryService
from app.modules.stocktake.models import Stocktake, StocktakeLine
from app.modules.stocktake.repository import StocktakeRepository
from app.shared.enums import StocktakeStatus
from app.shared.exceptions import StocktakeLineInvalid, StocktakeNotDraft, StocktakeNotFound

_SNAPSHOT_CAP = 100_000  # 單店 catalog 商品數遠小於此；一次快照全部。


class StocktakeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = StocktakeRepository(session)
        self._inventory = InventoryService(session)

    async def create_stocktake(self, store_id: int, *, actor_user_id: int) -> Stocktake:
        """建立盤點單：為店內每個一般商品快照當前 system_qty（DRAFT、counted 未填）。"""
        products = await self._inventory.list_catalog(store_id, limit=_SNAPSHOT_CAP, offset=0)
        stocktake = await self._repo.add_stocktake(
            Stocktake(store_id=store_id, created_by=actor_user_id)
        )
        for product in products:
            await self._repo.add_line(
                StocktakeLine(
                    store_id=store_id,
                    stocktake_id=stocktake.id,
                    catalog_product_id=product.id,
                    system_qty=product.quantity_on_hand,
                )
            )
        await self._session.refresh(stocktake, attribute_names=["lines"])
        return stocktake

    async def get_stocktake(self, store_id: int, stocktake_id: int) -> Stocktake | None:
        return await self._repo.get_stocktake(store_id, stocktake_id)

    async def list_stocktakes(
        self, store_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[Stocktake]:
        return await self._repo.list_stocktakes(store_id, limit=limit, offset=offset)

    async def confirm_stocktake(
        self,
        store_id: int,
        stocktake_id: int,
        counts: Mapping[int, int],
        *,
        actor_user_id: int,
    ) -> Stocktake:
        """確認盤點：依實點數即時校正現量並寫 ADJUST 帳；DRAFT→CONFIRMED（僅一次）。

        - 不存在/他店 → StocktakeNotFound；非 DRAFT → StocktakeNotDraft。
        - 實點商品不在本盤點單 → StocktakeLineInvalid。
        - 以盤點單列鎖（FOR UPDATE）+ 狀態守衛防重複確認/重複調整。
        """
        stocktake = await self._repo.lock_stocktake(store_id, stocktake_id)
        if stocktake is None:
            raise StocktakeNotFound(f"找不到盤點單 {stocktake_id}")
        if stocktake.status != StocktakeStatus.DRAFT:
            raise StocktakeNotDraft(
                f"盤點單 {stocktake_id} 狀態為 {stocktake.status.value}，不可確認"
            )
        lines_by_product = {line.catalog_product_id: line for line in stocktake.lines}
        adjusted = 0
        for catalog_product_id, counted_qty in counts.items():
            line = lines_by_product.get(catalog_product_id)
            if line is None:
                raise StocktakeLineInvalid(f"商品 {catalog_product_id} 不在盤點單 {stocktake_id}")
            line.counted_qty = counted_qty
            delta = await self._inventory.adjust_catalog_to_count(
                store_id, catalog_product_id, counted_qty, ref_type="stocktake", ref_id=stocktake.id
            )
            if delta != 0:
                adjusted += 1
        stocktake.status = StocktakeStatus.CONFIRMED
        stocktake.confirmed_at = datetime.now(UTC)
        stocktake.confirmed_by = actor_user_id
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="STOCKTAKE_CONFIRM",
            entity_type="stocktake",
            entity_id=str(stocktake.id),
            after={"counted_lines": len(counts), "adjusted_lines": adjusted},
        )
        await self._session.flush()
        refreshed = await self._repo.get_stocktake(store_id, stocktake.id)
        assert refreshed is not None  # 同交易內剛確認，必存在
        return refreshed
