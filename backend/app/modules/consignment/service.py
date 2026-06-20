"""consignment 業務邏輯：賣出寄售品時建立 PENDING 結算；付款給寄售人（Phase 4 / 4A）。

抽成與應付以 core/money 在整數元域計算（§7.2）。付款＝應付（售價−抽成）現金出帳並轉 PAID，
須在開帳中的 cash_session 下進行（invariant #8、#4）。作廢銷售時的結算反轉（invariant #7：
未付→CANCELLED、已付→reclaim_needed）由 cancel_settlements_for_sale 處理（供 void_sale /
returns 呼叫）。
"""

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog, write_audit_log
from app.core.money import commission
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.models import ConsignmentSettlement
from app.modules.consignment.repository import ConsignmentRepository
from app.shared.enums import CashMovementType, ConsignmentSettlementStatus
from app.shared.exceptions import IdempotencyKeyConflict, SettlementNotFound, SettlementNotPending


class ConsignmentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ConsignmentRepository(session)
        self._cashdrawer = CashDrawerService(session)

    async def pay_settlement(
        self,
        store_id: int,
        settlement_id: int,
        *,
        actor_user_id: int,
        idempotency_key: str,
    ) -> ConsignmentSettlement:
        """付款給寄售人（payout = 售價 − 抽成）：現金出帳並結算轉 PAID（Phase 4 / 4A）。

        - 不存在/他店 → SettlementNotFound；非 PENDING（已付/已取消）→ SettlementNotPending。
        - 無開帳 cash_session → NoOpenCashSession（record_movement 內擋；invariant #8）。
        - idempotency_key 必填；同 key 重送回同一 PAID 結果，不重複出帳。
        - 現金出帳走 CONSIGNMENT_PAYOUT_OUT，對帳 expected 已內含此型之扣減（invariant #4），
          故付款後抽屜淨增 = 抽成（店家真正收入），不是全額售價（§7.3）。
        - 併發/重送：以 settlement 列鎖（get_for_update）+ 狀態為準，只一筆成功、不重複出帳。
          鎖順序：settlement 列 → cash_session（record_movement 內鎖），全程同一交易。
        """
        key = self._normalize_idempotency_key(idempotency_key)
        await self._lock_idempotency_key(store_id, key)
        settlement = await self._repo.get_for_update(store_id, settlement_id)
        if settlement is None:
            raise SettlementNotFound(f"找不到寄售結算 {settlement_id}")
        if settlement.status != ConsignmentSettlementStatus.PENDING:
            replay = await self._payout_audit_for_key(store_id, key)
            if settlement.status == ConsignmentSettlementStatus.PAID and self._audit_matches_payout(
                replay, settlement_id
            ):
                return settlement
            raise SettlementNotPending(
                f"寄售結算 {settlement_id} 狀態為 {settlement.status.value}，不可付款"
            )
        replay = await self._payout_audit_for_key(store_id, key)
        if replay is not None:
            if self._audit_matches_payout(replay, settlement_id):
                return settlement
            raise IdempotencyKeyConflict("Idempotency-Key 已用於不同寄售付款")
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
                "idempotency_key": key,
            },
        )
        await self._session.flush()
        return settlement

    @staticmethod
    def _normalize_idempotency_key(idempotency_key: str) -> str:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise IdempotencyKeyConflict("idempotency_key 必須為非空字串")
        return idempotency_key.strip()

    async def _payout_audit_for_key(self, store_id: int, idempotency_key: str) -> AuditLog | None:
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.store_id == store_id,
                AuditLog.action == "CONSIGNMENT_PAYOUT",
                AuditLog.after["idempotency_key"].as_string() == idempotency_key,
            )
            .order_by(AuditLog.id.desc())
            .limit(1)
        )
        return cast(AuditLog | None, await self._session.scalar(stmt))

    async def _lock_idempotency_key(self, store_id: int, idempotency_key: str) -> None:
        seed = f"consignment-payout:{store_id}:{idempotency_key}".encode()
        digest = hashlib.sha256(seed).digest()
        lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
        )

    @staticmethod
    def _audit_matches_payout(audit: AuditLog | None, settlement_id: int) -> bool:
        if audit is None:
            return False
        return audit.entity_id == str(settlement_id)

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
        return await self._reverse_settlements(store_id, settlements, actor_user_id=actor_user_id)

    async def cancel_settlement_for_sale_item(
        self, store_id: int, sale_id: int, serialized_item_id: int, *, actor_user_id: int
    ) -> list[ConsignmentSettlement]:
        """退回該銷售中某寄售序號品時反轉其結算（invariant #7；部分退貨用）。

        多品項銷售只退寄售品時，整張單尚未 RETURNED，但該寄售品已退回，其結算必須反轉
        （未付→CANCELLED、已付→reclaim_needed），否則仍可被付款給寄售人、對已退商品漏付現金
        （Codex High）。非寄售序號品（無結算）→ no-op。退貨流程在**現金出帳前**先取得結算列鎖，
        建立『結算 → cash_session』鎖序與 pay_settlement 一致，避免退貨↔付款死結（Codex High）。
        """
        settlements = await self._repo.list_for_sale_item_for_update(
            store_id, sale_id, serialized_item_id
        )
        return await self._reverse_settlements(store_id, settlements, actor_user_id=actor_user_id)

    async def _reverse_settlements(
        self,
        store_id: int,
        settlements: list[ConsignmentSettlement],
        *,
        actor_user_id: int,
    ) -> list[ConsignmentSettlement]:
        """反轉已鎖定的結算列（PENDING→CANCELLED；PAID→reclaim_needed；其餘 no-op）並留痕。"""
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
    ) -> list[dict[str, Any]]:
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
