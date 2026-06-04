"""cashdrawer 業務邏輯：開帳/結帳/現金異動與對帳。

開帳安全性：以 cash_sessions 的 partial unique index（同 store 至多一個 OPEN）為最終保證，
故併發開帳由 DB 約束擋下（非僅靠先查再開）。
expected 一律以 core/money 在 Decimal/整數元域計算，無 float。
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.core.money import round_ntd
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.repository import CashDrawerRepository
from app.shared.enums import CashMovementType, CashSessionStatus
from app.shared.exceptions import (
    CashSessionAlreadyClosed,
    CashSessionAlreadyOpen,
    NoOpenCashSession,
    UnknownCashMovementType,
)


class CashDrawerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = CashDrawerRepository(session)

    async def get_current_session(self, store_id: int) -> CashSession | None:
        return await self._repo.get_open_session(store_id)

    async def open_session(
        self, store_id: int, opened_by: int, opening_float: Decimal
    ) -> CashSession:
        """開帳；同 store 已有 OPEN 則拒絕（DB partial unique 為最終保證，擋併發）。"""
        if await self._repo.get_open_session(store_id) is not None:
            raise CashSessionAlreadyOpen(f"store {store_id} 已有開帳中的 cash_session")
        cash_session = CashSession(
            store_id=store_id, opened_by=opened_by, opening_float=opening_float
        )
        try:
            return await self._repo.add_session(cash_session)
        except IntegrityError as exc:  # 併發競態：另一筆先開成功
            raise CashSessionAlreadyOpen(f"store {store_id} 已有開帳中的 cash_session") from exc

    async def record_movement(
        self,
        store_id: int,
        movement_type: CashMovementType,
        amount: Decimal,
        *,
        actor_user_id: int | None = None,
        ref_type: str | None = None,
        ref_id: int | None = None,
    ) -> CashMovement:
        """記一筆現金異動；必須在開帳中的 session 下進行，否則拒絕。

        MANUAL_ADJUST 屬「現金調整」敏感操作，須寫 audit_log（CLAUDE.md §5）；
        SALE_IN / BUYOUT_OUT / CONSIGNMENT_PAYOUT_OUT 由各自上游交易稽核，不在此重複。
        """
        session = await self._repo.get_open_session(store_id)
        if session is None:
            raise NoOpenCashSession("無開帳中的 cash_session，請先開帳")
        movement = CashMovement(
            store_id=store_id,
            session_id=session.id,
            type=movement_type,
            amount=amount,
            ref_type=ref_type,
            ref_id=ref_id,
        )
        saved = await self._repo.add_movement(movement)
        if movement_type == CashMovementType.MANUAL_ADJUST:
            await write_audit_log(
                self._session,
                store_id=store_id,
                actor_user_id=actor_user_id,
                action="CASH_MANUAL_ADJUST",
                entity_type="cash_session",
                entity_id=str(session.id),
                after={
                    "amount": str(amount),
                    "ref_type": ref_type,
                    "ref_id": ref_id,
                },
            )
        return saved

    async def expected_amount(self, session: CashSession) -> Decimal:
        """結帳應有現金 = 開帳零用金 + ΣSALE_IN − ΣBUYOUT_OUT − ΣPAYOUT_OUT ± ΣMANUAL_ADJUST。"""
        total = session.opening_float
        for movement in await self._repo.list_movements(session.id):
            if movement.type == CashMovementType.SALE_IN:
                total += movement.amount
            elif movement.type in (
                CashMovementType.BUYOUT_OUT,
                CashMovementType.CONSIGNMENT_PAYOUT_OUT,
            ):
                total -= movement.amount
            elif movement.type == CashMovementType.MANUAL_ADJUST:
                total += movement.amount  # 金額可正可負
            else:  # 未知類型：拒絕靜默計算，以免算錯現金
                raise UnknownCashMovementType(f"未知現金異動類型：{movement.type!r}")
        return Decimal(round_ntd(total))

    async def close_session(
        self, session: CashSession, counted_amount: Decimal, closed_by: int
    ) -> CashSession:
        """結帳：計算 expected 與 variance、轉 CLOSED，並寫 audit_log（現金對帳，CLAUDE.md §5）。"""
        if session.status != CashSessionStatus.OPEN:
            raise CashSessionAlreadyClosed(f"cash_session {session.id} 已結帳，不可重複結帳")
        expected = await self.expected_amount(session)
        session.expected_amount = expected
        session.counted_amount = counted_amount
        session.variance = counted_amount - expected
        session.status = CashSessionStatus.CLOSED
        session.closed_by = closed_by
        session.closed_at = datetime.now(UTC)
        await self._session.flush()
        await write_audit_log(
            self._session,
            store_id=session.store_id,
            actor_user_id=closed_by,
            action="CLOSE_CASH_SESSION",
            entity_type="cash_session",
            entity_id=str(session.id),
            before={"status": CashSessionStatus.OPEN.value},
            after={
                "status": CashSessionStatus.CLOSED.value,
                "expected_amount": str(expected),
                "counted_amount": str(counted_amount),
                "variance": str(session.variance),
            },
        )
        return session
