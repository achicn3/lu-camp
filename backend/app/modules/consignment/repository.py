"""consignment 資料存取層（唯一直接碰 ORM 的層）。

T11 售出時新增 PENDING 結算；T21-b 加會員中心唯讀查詢。付款（→PAID）等屬 Phase 4。
跨模組邊界：本層只碰 consignment_settlements 自身；寄售人↔結算的關聯由 facade 以
inventory 提供的 serialized_item_ids 串接（不直接查 inventory 表）。
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.consignment.models import ConsignmentSettlement
from app.shared.enums import ConsignmentSettlementStatus


class ConsignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, settlement: ConsignmentSettlement) -> ConsignmentSettlement:
        self._session.add(settlement)
        await self._session.flush()
        return settlement

    async def list_by_item_ids(
        self, store_id: int, serialized_item_ids: list[int], *, limit: int, offset: int
    ) -> list[ConsignmentSettlement]:
        """指定序號品的結算列（store 範圍、新到舊、分頁；空 ids → 空清單）。"""
        if not serialized_item_ids:
            return []
        stmt = (
            select(ConsignmentSettlement)
            .where(
                ConsignmentSettlement.store_id == store_id,
                ConsignmentSettlement.serialized_item_id.in_(serialized_item_ids),
            )
            .order_by(ConsignmentSettlement.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.scalars(stmt)).all())

    async def pending_payout_total_by_item_ids(
        self, store_id: int, serialized_item_ids: list[int]
    ) -> Decimal:
        """指定序號品的 PENDING 應撥加總（Σ payout_amount；空 ids → 0；SQL 聚合）。"""
        if not serialized_item_ids:
            return Decimal(0)
        stmt = select(func.coalesce(func.sum(ConsignmentSettlement.payout_amount), 0)).where(
            ConsignmentSettlement.store_id == store_id,
            ConsignmentSettlement.serialized_item_id.in_(serialized_item_ids),
            ConsignmentSettlement.status == ConsignmentSettlementStatus.PENDING,
        )
        total = await self._session.scalar(stmt)
        return Decimal(total if total is not None else 0)
