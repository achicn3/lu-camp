"""散裝販售籃的資料存取（ADR-025）。唯一直接碰 bulk_baskets 的層。"""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inventory.models import BulkBasket, BulkLot
from app.shared.enums import BulkLotStatus

_LIST_LIMIT = 200


class BulkBasketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, basket: BulkBasket) -> BulkBasket:
        self._session.add(basket)
        await self._session.flush()
        return basket

    async def get(self, store_id: int, basket_id: int) -> BulkBasket | None:
        stmt = select(BulkBasket).where(BulkBasket.id == basket_id, BulkBasket.store_id == store_id)
        result: BulkBasket | None = await self._session.scalar(stmt)
        return result

    async def get_for_update(self, store_id: int, basket_id: int) -> BulkBasket | None:
        """鎖籃子列：改價、入籃與結帳分配都先鎖籃，彼此序列化。"""
        stmt = (
            select(BulkBasket)
            .where(BulkBasket.id == basket_id, BulkBasket.store_id == store_id)
            .with_for_update()
        )
        result: BulkBasket | None = await self._session.scalar(stmt)
        return result

    async def get_by_code(self, store_id: int, code: str) -> BulkBasket | None:
        stmt = select(BulkBasket).where(BulkBasket.code == code, BulkBasket.store_id == store_id)
        result: BulkBasket | None = await self._session.scalar(stmt)
        return result

    async def list_baskets(
        self, store_id: int, *, q: str | None, include_inactive: bool
    ) -> list[BulkBasket]:
        stmt = select(BulkBasket).where(BulkBasket.store_id == store_id)
        if not include_inactive:
            stmt = stmt.where(BulkBasket.is_active.is_(True))
        if q:
            stmt = stmt.where(BulkBasket.name.ilike(f"%{q}%"))
        stmt = stmt.order_by(BulkBasket.name, BulkBasket.id).limit(_LIST_LIMIT)
        return list((await self._session.scalars(stmt)).all())

    async def sources_by_basket(
        self, store_id: int, basket_ids: list[int]
    ) -> dict[int, list[BulkLot]]:
        """各籃來源（排除作廢收購退場的 WRITTEN_OFF），依入庫先後（FIFO 同序）；單一查詢。"""
        grouped: dict[int, list[BulkLot]] = {bid: [] for bid in basket_ids}
        if not basket_ids:
            return grouped
        stmt = (
            select(BulkLot)
            .where(
                BulkLot.store_id == store_id,
                BulkLot.basket_id.in_(basket_ids),
                BulkLot.status != BulkLotStatus.WRITTEN_OFF,
            )
            .order_by(BulkLot.intake_date, BulkLot.id)
        )
        for lot in (await self._session.scalars(stmt)).all():
            if lot.basket_id is not None:
                grouped[lot.basket_id].append(lot)
        return grouped

    async def sources_for_update(self, store_id: int, basket_id: int) -> list[BulkLot]:
        """鎖籃內全部來源。鎖序固定依 id，避免兩筆交易交叉鎖列而死結。"""
        stmt = (
            select(BulkLot)
            .where(BulkLot.store_id == store_id, BulkLot.basket_id == basket_id)
            .order_by(BulkLot.id)
            .with_for_update()
        )
        return list((await self._session.scalars(stmt)).all())

    async def lock_for_sale(self, store_id: int, basket_ids: list[int], lot_ids: list[int]) -> None:
        """結帳前置：先依 id 鎖本單所有籃子，再依 id 鎖這些籃的來源與直接指名的散裝。

        之後逐行（購物車序）的扣減只再觸碰已持有的鎖；兩台收銀以相反順序賣同幾籃時
        不會互卡（AB-BA 死結）。籃先於來源，與改籃價／入籃的鎖序一致。
        """
        if basket_ids:
            await self._session.execute(
                select(BulkBasket.id)
                .where(BulkBasket.store_id == store_id, BulkBasket.id.in_(basket_ids))
                .order_by(BulkBasket.id)
                .with_for_update()
            )
        if basket_ids or lot_ids:
            await self._session.execute(
                select(BulkLot.id)
                .where(
                    BulkLot.store_id == store_id,
                    or_(BulkLot.basket_id.in_(basket_ids), BulkLot.id.in_(lot_ids)),
                )
                .order_by(BulkLot.id)
                .with_for_update()
            )
