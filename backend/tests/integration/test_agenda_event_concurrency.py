"""行事曆事件「取消某一次」的併發（Codex 1A 第四輪 P2）。

兩台裝置同時取消同一個重複事件的不同日子：取消清單是「讀整個陣列→改→寫回」，不鎖的話
後提交的會把先提交的那一次蓋掉，被取消的公休日又冒回月曆。

以確定性交錯驗證列鎖：A 取消 11/9 後持鎖不提交 → B 取消 11/16（撞鎖等待）→ A 提交 →
B 於鎖內讀到最新的陣列再加上自己的 → 兩個日子都在。需真正兩條交易，故用獨立 session，
結束在 finally 清掉自建列。
"""

import asyncio
from datetime import date

from sqlalchemy import delete

import app.core.db as app_db
from app.modules.agenda.models import AgendaEvent
from app.modules.agenda.schemas import AgendaEventCreate, RecurrenceFields
from app.modules.agenda.service import AgendaService, Viewer
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import AgendaEventKind, RecurrenceKind, UserRole


async def test_concurrent_occurrence_cancellations_both_survive() -> None:
    sm = app_db.get_sessionmaker()
    async with sm() as s:
        store = Store(name="併發行事曆店")
        s.add(store)
        await s.flush()
        actor = User(
            store_id=store.id, username="agenda-cc", password_hash="h", role=UserRole.MANAGER
        )
        s.add(actor)
        await s.flush()
        viewer = Viewer(user_id=actor.id, is_manager=True)
        event = await AgendaService(s).create_event(
            store.id,
            viewer,
            AgendaEventCreate(
                kind=AgendaEventKind.CLOSED,
                title="公休",
                starts_on=date(2026, 11, 2),
                recurrence=RecurrenceFields(kind=RecurrenceKind.WEEKLY, weekdays=[0]),
            ),
        )
        store_id, event_id = store.id, event.id
        await s.commit()

    session_a = sm()
    session_b = sm()
    try:
        await AgendaService(session_a).set_event_occurrence_cancelled(
            store_id, viewer, event_id, date(2026, 11, 9), cancelled=True
        )

        async def cancel_other_day() -> None:
            await AgendaService(session_b).set_event_occurrence_cancelled(
                store_id, viewer, event_id, date(2026, 11, 16), cancelled=True
            )
            await session_b.commit()

        b_task = asyncio.create_task(cancel_other_day())
        await asyncio.sleep(0.3)  # 讓 B 抵達鎖等待點（A 尚持鎖時）
        assert not b_task.done()  # B 被列鎖擋住；沒有鎖的話這裡已經做完（並蓋掉 A）

        await session_a.commit()
        await b_task

        async with sm() as check:
            saved = await check.get(AgendaEvent, event_id)
            assert saved is not None
            assert saved.cancelled_dates == [date(2026, 11, 9), date(2026, 11, 16)]
    finally:
        await session_a.close()
        await session_b.close()
        async with sm() as cleanup:
            await cleanup.execute(delete(AgendaEvent).where(AgendaEvent.store_id == store_id))
            await cleanup.execute(delete(User).where(User.store_id == store_id))
            await cleanup.execute(delete(Store).where(Store.id == store_id))
            await cleanup.commit()
