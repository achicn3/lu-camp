"""consignment 業務邏輯：賣出寄售品時建立 PENDING 結算。

抽成與應付以 core/money 在整數元域計算（§7.2）。付款/退貨反轉屬 Phase 4，本模組此階段
只提供售出時的結算建立，供 sales 在同一交易內呼叫。
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import commission
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.consignment.repository import ConsignmentRepository


class ConsignmentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ConsignmentRepository(session)

    async def create_settlement(
        self,
        store_id: int,
        *,
        serialized_item_id: int,
        sale_id: int,
        gross: Decimal,
        commission_pct: int,
    ) -> ConsignmentSettlement:
        """賣出寄售品 → 建 PENDING 結算。

        commission_amount = round_ntd(gross × pct / 100)；payout = gross − commission。
        店家收入只認 commission_amount（§7.3）。
        """
        commission_amount = commission(gross, commission_pct)
        payout = gross - Decimal(commission_amount)
        settlement = ConsignmentSettlement(
            store_id=store_id,
            serialized_item_id=serialized_item_id,
            sale_id=sale_id,
            gross=gross,
            commission_pct=commission_pct,
            commission_amount=Decimal(commission_amount),
            payout_amount=payout,
        )
        return await self._repo.add(settlement)

    async def list_settlements_by_item_ids(
        self, store_id: int, serialized_item_ids: list[int], *, limit: int = 50, offset: int = 0
    ) -> list[ConsignmentSettlement]:
        """指定序號品的寄售結算列（會員中心；序號品 id 由 facade 自 inventory 取得）。"""
        return await self._repo.list_by_item_ids(
            store_id, serialized_item_ids, limit=limit, offset=offset
        )

    async def pending_payout_total_by_item_ids(
        self, store_id: int, serialized_item_ids: list[int]
    ) -> Decimal:
        """指定序號品的 PENDING 應撥加總（會員中心 overview/consignments；docs/17 §3.4）。"""
        return await self._repo.pending_payout_total_by_item_ids(store_id, serialized_item_ids)

    async def latest_settlement_by_item_ids(
        self, store_id: int, serialized_item_ids: list[int]
    ) -> dict[int, ConsignmentSettlement]:
        """每序號品最新一筆結算（會員中心寄售清單；一 SQL 取回，不漏品）。"""
        return await self._repo.latest_settlement_by_item_ids(store_id, serialized_item_ids)
