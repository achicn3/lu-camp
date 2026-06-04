"""cashdrawer 併發不變量：同一 store 同時只允許一個 OPEN cash_session。

需要真正的兩條交易並行，故用獨立 session（各自 commit），不走 db_session 回滾隔離；
保證由 cash_sessions 的 partial unique index（status='OPEN'）擋下，而非僅靠先查再開。
測試結束在 finally 清掉自建的列，不留殘餘。
"""

import asyncio
from collections.abc import Awaitable, Callable
from decimal import Decimal

from sqlalchemy import delete, select

import app.core.db as app_db
from app.modules.cashdrawer.models import CashSession
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import CashSessionStatus, UserRole
from app.shared.exceptions import CashSessionAlreadyOpen


async def _run_twice(op: Callable[[], Awaitable[bool]]) -> int:
    """並行跑兩次，回傳成功次數。"""
    results = await asyncio.gather(op(), op())
    return sum(results)


async def test_concurrent_open_only_one_succeeds() -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發現金店")
        s.add(store)
        await s.flush()
        user = User(
            store_id=store.id,
            username="conc-clerk",
            password_hash="h",
            role=UserRole.CLERK,
        )
        s.add(user)
        await s.flush()
        store_id, user_id = store.id, user.id
        await s.commit()

    try:

        async def open_session() -> bool:
            async with sm() as s:
                try:
                    await CashDrawerService(s).open_session(store_id, user_id, Decimal("1000"))
                    await s.commit()
                    return True
                except CashSessionAlreadyOpen:
                    await s.rollback()
                    return False

        # 兩條獨立交易同時開帳 → partial unique index 只放行一筆。
        assert await _run_twice(open_session) == 1

        async with sm() as s:
            open_sessions = (
                await s.scalars(
                    select(CashSession).where(
                        CashSession.store_id == store_id,
                        CashSession.status == CashSessionStatus.OPEN,
                    )
                )
            ).all()
            assert len(open_sessions) == 1
    finally:
        async with sm() as s:
            await s.execute(delete(CashSession).where(CashSession.store_id == store_id))
            await s.execute(delete(User).where(User.id == user_id))
            await s.execute(delete(Store).where(Store.id == store_id))
            await s.commit()
