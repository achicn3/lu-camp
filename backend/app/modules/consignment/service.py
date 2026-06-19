"""consignment 業務邏輯：賣出寄售品時建立 PENDING 結算；付款給寄售人（Phase 4 / 4A）。

抽成與應付以 core/money 在整數元域計算（§7.2）。付款＝應付（售價−抽成）現金出帳並轉 PAID，
須在開帳中的 cash_session 下進行（invariant #8、#4）。作廢銷售時的結算反轉（invariant #7：
未付→CANCELLED、已付→reclaim_needed）由 cancel_settlements_for_sale 處理（供 void_sale 呼叫）；
退貨退現/庫存回補屬後續切片（4B）。
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import commission
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.consignment.repository import ConsignmentRepository
from app.shared.enums import CashMovementType, ConsignmentSettlementStatus
from app.shared.exceptions import SettlementNotFound, SettlementNotPending


class ConsignmentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ConsignmentRepository(session)
        self._cashdrawer = CashDrawerService(session)

    async def pay_settlement(
        self, store_id: int, settlement_id: int, *, actor_user_id: int
    ) -> ConsignmentSettlement:
        """付款給寄售人（payout = 售價 − 抽成）：現金出帳並結算轉 PAID（Phase 4 / 4A）。

        - 不存在/他店 → SettlementNotFound；非 PENDING（已付/已取消）→ SettlementNotPending。
        - 無開帳 cash_session → NoOpenCashSession（record_movement 內擋；invariant #8）。
        - 現金出帳走 CONSIGNMENT_PAYOUT_OUT，對帳 expected 已內含此型之扣減（invariant #4），
          故付款後抽屜淨增 = 抽成（店家真正收入），不是全額售價（§7.3）。
        - 併發/重送：以 settlement 列鎖（get_for_update）+ 狀態為準，只一筆成功、不重複出帳。
          鎖順序：settlement 列 → cash_session（record_movement 內鎖），全程同一交易。
        """
        settlement = await self._repo.get_for_update(store_id, settlement_id)
        if settlement is None:
            raise SettlementNotFound(f"找不到寄售結算 {settlement_id}")
        if settlement.status != ConsignmentSettlementStatus.PENDING:
            raise SettlementNotPending(
                f"寄售結算 {settlement_id} 狀態為 {settlement.status.value}，不可付款"
            )
        # 現金出帳（無開帳即於此擋下，settlement 尚未變更 → 整筆回滾不留半套）。
        await self._cashdrawer.record_movement(
            store_id,
            CashMovementType.CONSIGNMENT_PAYOUT_OUT,
            settlement.payout_amount,
            actor_user_id=actor_user_id,
            ref_type="consignment_settlement",
            ref_id=settlement.id,
        )
        settlement.status = ConsignmentSettlementStatus.PAID
        settlement.paid_at = datetime.now(UTC)
        settlement.paid_by = actor_user_id
        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="CONSIGNMENT_PAYOUT",
            entity_type="consignment_settlement",
            entity_id=str(settlement.id),
            before={"status": ConsignmentSettlementStatus.PENDING.value},
            after={
                "status": ConsignmentSettlementStatus.PAID.value,
                "payout_amount": str(settlement.payout_amount),
            },
        )
        await self._session.flush()
        return settlement

    async def cancel_settlements_for_sale(
        self, store_id: int, sale_id: int, *, actor_user_id: int
    ) -> list[ConsignmentSettlement]:
        """作廢/退回該銷售時反轉其寄售結算（invariant #7）。

        未付（PENDING）→ CANCELLED（不再列為應付）；已付（PAID）→ reclaim_needed=True
        （錢已付出，須向寄售人追回，不可靜默抹除已實現抽成/應付）；已 CANCELLED / 已標
        reclaim → no-op。供 sales.void_sale 在同一交易內呼叫；以結算列鎖（FOR UPDATE）與
        pay_settlement 互斥——作廢/付款競態只一方生效，不會「既付款又取消」。
        """
        settlements = await self._repo.list_for_sale_for_update(store_id, sale_id)
        reversed_rows: list[ConsignmentSettlement] = []
        for settlement in settlements:
            before_status = settlement.status
            if before_status == ConsignmentSettlementStatus.PENDING:
                settlement.status = ConsignmentSettlementStatus.CANCELLED
                after: dict[str, object] = {"status": ConsignmentSettlementStatus.CANCELLED.value}
            elif (
                before_status == ConsignmentSettlementStatus.PAID and not settlement.reclaim_needed
            ):
                settlement.reclaim_needed = True
                after = {"status": before_status.value, "reclaim_needed": True}
            else:
                continue
            await write_audit_log(
                self._session,
                store_id=store_id,
                actor_user_id=actor_user_id,
                action="CONSIGNMENT_SETTLEMENT_REVERSE",
                entity_type="consignment_settlement",
                entity_id=str(settlement.id),
                before={"status": before_status.value},
                after=after,
            )
            reversed_rows.append(settlement)
        if reversed_rows:
            await self._session.flush()
        return reversed_rows

    async def list_settlements(
        self,
        store_id: int,
        *,
        status: ConsignmentSettlementStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ConsignmentSettlement]:
        """店內寄售結算列（可篩 status；付款工作清單/應付查詢；§4 店別範圍）。"""
        return await self._repo.list_settlements(
            store_id, status=status, limit=limit, offset=offset
        )

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

    async def commission_total_for_sales(self, store_id: int, sale_ids: list[int]) -> Decimal:
        """指定銷售集合的寄售抽成合計（SC-5b §5B 毛利；唯讀，§2 經 service）。"""
        return await self._repo.commission_total_for_sales(store_id, sale_ids)
