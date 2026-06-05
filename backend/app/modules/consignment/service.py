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
