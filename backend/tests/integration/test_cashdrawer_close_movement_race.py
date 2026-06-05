"""cashdrawer 併發不變量（D-1）：關帳與現金異動互斥，異動不會落進已關閉的 session。

真並行（asyncio.gather 兩條獨立交易）：一邊關帳、一邊插入 SALE_IN。由 cash_session 列的
FOR UPDATE 鎖序列化（record_movement 與 close_session 互斥），保證最終狀態一致：
若 SALE_IN 落地，則關帳 expected 必含它；若關帳先成，則 SALE_IN 被拒（NoOpenCashSession）。
兩種結果都不得出現「異動進了已關閉 session 卻被 expected 漏算」。

此保證在 record_movement 單一處強化，故 T6（MANUAL_ADJUST）/T7（BUYOUT_OUT）/T11（SALE_IN）
的現金寫入皆受其保護。
"""

import asyncio
from decimal import Decimal

from sqlalchemy import delete, func, select

import app.core.db as app_db
from app.core.audit import AuditLog
from app.modules.cashdrawer.models import CashMovement, CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import CashMovementType, CashSessionStatus, UserRole
from app.shared.exceptions import CashSessionAlreadyClosed, NoOpenCashSession

OPENING = Decimal("1000")
SALE_IN = Decimal("500")


async def test_close_and_sale_in_race_keeps_reconciliation_consistent() -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="關帳競態店")
        s.add(store)
        await s.flush()
        clerk = User(store_id=store.id, username="race-clk", password_hash="h", role=UserRole.CLERK)
        s.add(clerk)
        await s.flush()
        opened = await CashDrawerService(s).open_session(store.id, clerk.id, OPENING)
        store_id, clerk_id, session_id = store.id, clerk.id, opened.id
        await s.commit()

    try:

        async def do_close() -> str:
            async with sm() as s:
                svc = CashDrawerService(s)
                cs = await svc.get_session(store_id, session_id)
                assert cs is not None
                try:
                    await svc.close_session(cs, Decimal("1500"), clerk_id)
                    await s.commit()
                    return "closed"
                except CashSessionAlreadyClosed:
                    await s.rollback()
                    return "already-closed"

        async def do_sale_in() -> str:
            async with sm() as s:
                svc = CashDrawerService(s)
                try:
                    await svc.record_movement(
                        store_id,
                        CashMovementType.SALE_IN,
                        SALE_IN,
                        actor_user_id=clerk_id,
                        ref_type="sale",
                        ref_id=1,
                    )
                    await s.commit()
                    return "inserted"
                except NoOpenCashSession:
                    await s.rollback()
                    return "rejected"

        await asyncio.gather(do_close(), do_sale_in())

        async with sm() as s:
            session = await s.get(CashSession, session_id)
            assert session is not None
            assert session.status == CashSessionStatus.CLOSED  # 關帳必定完成
            sale_in_count = await s.scalar(
                select(func.count())
                .select_from(CashMovement)
                .where(
                    CashMovement.session_id == session_id,
                    CashMovement.type == CashMovementType.SALE_IN,
                )
            )
            # 核心不變量：若 SALE_IN 落地，expected 必含它；否則 expected 僅開帳零用金。
            # 不得出現「異動在帳上、卻沒被 expected 計入」。
            assert session.expected_amount == OPENING + SALE_IN * (sale_in_count or 0)
            assert session.variance == Decimal("1500") - session.expected_amount
    finally:
        async with sm() as s:
            await s.execute(delete(AuditLog).where(AuditLog.store_id == store_id))
            await s.execute(delete(CashMovement).where(CashMovement.store_id == store_id))
            await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
            await s.execute(delete(User).where(User.store_id == store_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()
