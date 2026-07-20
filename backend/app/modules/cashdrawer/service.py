"""cashdrawer 業務邏輯：開帳/結帳/現金異動與對帳。

開帳安全性：以 cash_sessions 的 partial unique index（同 store 至多一個 OPEN）為最終保證，
故併發開帳由 DB 約束擋下（非僅靠先查再開）。
expected 一律以 core/money 在 Decimal/整數元域計算，無 float。
"""

from dataclasses import dataclass
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


@dataclass(frozen=True)
class CashSessionBreakdown:
    """單一 session 的現金組成與 expected（唯讀報表用）。

    expected 以與關帳同一公式由各組成推得，故報表 expected 與關帳 `expected_amount` 同源
    （docs/19 §2.2）。purchasing/sales 等現金流皆已落 cash_movement，這裡只彙整、不重算業務。
    """

    session: CashSession
    cash_sales: Decimal
    acquisition_void_in: Decimal
    buyout_out: Decimal
    consignment_payout_out: Decimal
    sale_refund_out: Decimal
    manual_adjust_total: Decimal
    expected: Decimal


class CashDrawerService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = CashDrawerRepository(session)

    async def get_current_session(self, store_id: int) -> CashSession | None:
        return await self._repo.get_open_session(store_id)

    async def get_session(self, store_id: int, session_id: int) -> CashSession | None:
        return await self._repo.get_session(store_id, session_id)

    async def list_session_movements(self, session: CashSession) -> list[CashMovement]:
        """列出單一已驗證門市班別的現金異動，最新一筆在前。"""
        return await self._repo.list_movements(session.id)

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
        note: str | None = None,
    ) -> CashMovement:
        """記一筆現金異動；必須在開帳中的 session 下進行，否則拒絕。

        MANUAL_ADJUST 屬「現金調整」敏感操作，於此寫 audit_log（CLAUDE.md §5）。
        SALE_IN / BUYOUT_OUT / CONSIGNMENT_PAYOUT_OUT 為一般營業現金流，本身即以
        cash_movement 入帳，不在 §5 強制稽核之列，故此層不另寫 audit。

        併發保證：以 FOR UPDATE 鎖開帳中的 session 列，與 close_session 互斥（DB 層原子，
        非先查狀態再插入）。若關帳已先一步轉 CLOSED，這裡的條件式查詢即查不到 OPEN → 拒絕，
        避免現金異動落進已關閉的 session 而被對帳漏算。T6/T7/T11 的現金寫入都經此一處。
        """
        session = await self._repo.get_open_session(store_id, for_update=True)
        if session is None:
            raise NoOpenCashSession("無開帳中的 cash_session，請先開帳")
        movement = CashMovement(
            store_id=store_id,
            session_id=session.id,
            type=movement_type,
            amount=amount,
            ref_type=ref_type,
            ref_id=ref_id,
            note=note,
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
                    "note": note,
                    "ref_type": ref_type,
                    "ref_id": ref_id,
                },
            )
        return saved

    async def expected_amount(self, session: CashSession) -> Decimal:
        """結帳應有現金 = 開帳零用金 + Σ(SALE_IN, ACQUISITION_VOID_IN)
        − Σ(BUYOUT_OUT, PAYOUT_OUT, SALE_REFUND_OUT) ± ΣMANUAL_ADJUST。"""
        total = session.opening_float
        for movement in await self._repo.list_movements(session.id):
            if movement.type in (
                CashMovementType.SALE_IN,
                CashMovementType.ACQUISITION_VOID_IN,
            ):
                total += movement.amount
            elif movement.type in (
                CashMovementType.BUYOUT_OUT,
                CashMovementType.CONSIGNMENT_PAYOUT_OUT,
                CashMovementType.SALE_REFUND_OUT,
            ):
                total -= movement.amount
            elif movement.type == CashMovementType.MANUAL_ADJUST:
                total += movement.amount  # 金額可正可負
            else:  # 未知類型：拒絕靜默計算，以免算錯現金
                raise UnknownCashMovementType(f"未知現金異動類型：{movement.type!r}")
        return Decimal(round_ntd(total))

    async def list_sessions_in_range(
        self, store_id: int, start: datetime, end: datetime
    ) -> list[CashSession]:
        """opened_at ∈ [start, end) 的本店 session（唯讀，每日現金報表用）。"""
        return await self._repo.list_sessions_in_range(store_id, start, end)

    async def cash_out_in_range(self, store_id: int, start: datetime, end: datetime) -> Decimal:
        """[start, end)（依事件時間）現金出帳合計＝收購付現＋寄售付款（唯讀，趨勢報表用）。"""
        return await self._repo.cash_out_in_range(store_id, start, end)

    async def session_breakdown(self, session: CashSession) -> CashSessionBreakdown:
        """單一 session 的現金組成與 expected（唯讀）。

        expected：OPEN 即時以 `expected_amount` 重算（與關帳同公式）；CLOSED 取結帳當下落帳的
        `expected_amount`（已反映的對帳事實）。組成欄僅供呈現，不另驅動 expected，避免雙重口徑漂移。
        """
        sums = {t: Decimal(0) for t in CashMovementType}
        for movement in await self._repo.list_movements(session.id):
            sums[movement.type] += movement.amount
        if session.status == CashSessionStatus.CLOSED and session.expected_amount is not None:
            expected = session.expected_amount
        else:
            expected = await self.expected_amount(session)
        return CashSessionBreakdown(
            session=session,
            cash_sales=sums[CashMovementType.SALE_IN],
            acquisition_void_in=sums[CashMovementType.ACQUISITION_VOID_IN],
            buyout_out=sums[CashMovementType.BUYOUT_OUT],
            consignment_payout_out=sums[CashMovementType.CONSIGNMENT_PAYOUT_OUT],
            sale_refund_out=sums[CashMovementType.SALE_REFUND_OUT],
            manual_adjust_total=sums[CashMovementType.MANUAL_ADJUST],
            expected=expected,
        )

    async def close_session(
        self, session: CashSession, counted_amount: Decimal, closed_by: int
    ) -> CashSession:
        """結帳：計算 expected 與 variance、轉 CLOSED，並寫 audit_log（現金對帳，CLAUDE.md §5）。

        併發保證：先以 FOR UPDATE 鎖 session 列並刷新到已提交狀態，與 record_movement 互斥；
        確保「計算 expected → 轉 CLOSED」期間沒有現金異動偷插入，對帳數字不漏算。
        """
        locked = await self._repo.lock_session(session.store_id, session.id)
        if locked is None or locked.status != CashSessionStatus.OPEN:
            raise CashSessionAlreadyClosed(f"cash_session {session.id} 已結帳，不可重複結帳")
        session = locked
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
