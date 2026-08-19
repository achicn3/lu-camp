"""叫號系統的服務層規則（docs/38）：配號、跨日重置、跨店隔離、完成冪等。"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import STORE_TIME_ZONE
from app.modules.callticket.service import CallTicketService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import CallTicketStatus, UserRole
from app.shared.exceptions import CallTicketNotFound

pytestmark = pytest.mark.asyncio

YESTERDAY = datetime(2026, 8, 18, 5, 0, tzinfo=UTC)  # 台北 13:00
TODAY = datetime(2026, 8, 19, 5, 0, tzinfo=UTC)


class Seed:
    """兩間門市與兩個操作者。

    **不寫死 id**：序號會延續前面的測試，寫死 1/2 會在共用測試庫上隨機失敗
    （實測踩過：store_id=1 早已被別的測試用掉）。
    """

    def __init__(self, store_a: int, store_b: int, user_a: int, user_b: int) -> None:
        self.store_a = store_a
        self.store_b = store_b
        self.user_a = user_a
        self.user_b = user_b


@pytest_asyncio.fixture
async def seed(db_session: AsyncSession) -> Seed:
    a, b = Store(name="門市A"), Store(name="門市B")
    db_session.add_all([a, b])
    await db_session.flush()
    u1 = User(store_id=a.id, username="ct-u1", password_hash="h", role=UserRole.CLERK)
    u2 = User(store_id=a.id, username="ct-u2", password_hash="h", role=UserRole.CLERK)
    db_session.add_all([u1, u2])
    await db_session.flush()
    return Seed(a.id, b.id, u1.id, u2.id)


def _svc(db_session: AsyncSession) -> CallTicketService:
    return CallTicketService(db_session)


async def test_first_ticket_of_the_day_is_number_one(
    db_session: AsyncSession, seed: Seed
) -> None:
    ticket = await _svc(db_session).create(
        seed.store_a, name="王先生", actor_user_id=seed.user_a
    )
    assert ticket.ticket_no == 1
    assert ticket.status is CallTicketStatus.WAITING


async def test_numbers_increment_within_the_same_day(
    db_session: AsyncSession, seed: Seed
) -> None:
    svc = _svc(db_session)
    nos = [
        (await svc.create(seed.store_a, name=f"客{i}", actor_user_id=seed.user_a)).ticket_no
        for i in range(3)
    ]
    assert nos == [1, 2, 3]


async def test_numbers_reset_on_the_next_store_day(
    db_session: AsyncSession, seed: Seed
) -> None:
    """跨日必須從 1 重新開始——否則號碼會愈叫愈長，失去叫號的意義。"""
    svc = _svc(db_session)
    a = await svc.create(seed.store_a, name="昨天", actor_user_id=seed.user_a, now=YESTERDAY)
    b = await svc.create(seed.store_a, name="今天", actor_user_id=seed.user_a, now=TODAY)
    assert (a.ticket_no, b.ticket_no) == (1, 1)
    assert a.ticket_date != b.ticket_date


async def test_store_day_uses_taipei_not_utc(db_session: AsyncSession, seed: Seed) -> None:
    """**台北 00:30 與 08:30 必須是同一個營業日**。

    用 UTC 切的話台北 00:30 還算前一天，早上的號碼會莫名接續昨天。
    """
    svc = _svc(db_session)
    early = datetime(2026, 8, 19, 0, 30, tzinfo=STORE_TIME_ZONE)
    later = datetime(2026, 8, 19, 8, 30, tzinfo=STORE_TIME_ZONE)
    a = await svc.create(seed.store_a, name="早", actor_user_id=seed.user_a, now=early)
    b = await svc.create(seed.store_a, name="晚", actor_user_id=seed.user_a, now=later)
    assert a.ticket_date == b.ticket_date
    assert (a.ticket_no, b.ticket_no) == (1, 2)


async def test_numbers_are_isolated_between_stores(
    db_session: AsyncSession, seed: Seed
) -> None:
    svc = _svc(db_session)
    a = await svc.create(seed.store_a, name="A 店", actor_user_id=seed.user_a)
    b = await svc.create(seed.store_b, name="B 店", actor_user_id=seed.user_a)
    assert (a.ticket_no, b.ticket_no) == (1, 1)


async def test_complete_marks_done_and_records_who(
    db_session: AsyncSession, seed: Seed
) -> None:
    svc = _svc(db_session)
    ticket = await svc.create(seed.store_a, name="王先生", actor_user_id=seed.user_a)
    done = await svc.complete(seed.store_a, ticket.id, actor_user_id=seed.user_b)
    assert done.status is CallTicketStatus.DONE
    assert done.completed_by_user_id == seed.user_b
    assert done.completed_at is not None


async def test_complete_is_idempotent(db_session: AsyncSession, seed: Seed) -> None:
    """手滑連按兩下、或兩台裝置同時按，都不該有人看到錯誤。"""
    svc = _svc(db_session)
    ticket = await svc.create(seed.store_a, name="王先生", actor_user_id=seed.user_a)
    first = await svc.complete(seed.store_a, ticket.id, actor_user_id=seed.user_a)
    second = await svc.complete(seed.store_a, ticket.id, actor_user_id=seed.user_b)
    assert second.status is CallTicketStatus.DONE
    # 第一次的人與時間才是事實，不可被後來的覆寫
    assert second.completed_by_user_id == seed.user_a
    assert second.completed_at == first.completed_at


async def test_complete_rejects_other_stores_ticket(
    db_session: AsyncSession, seed: Seed
) -> None:
    svc = _svc(db_session)
    ticket = await svc.create(seed.store_b, name="B 店", actor_user_id=seed.user_a)
    with pytest.raises(CallTicketNotFound):
        await svc.complete(seed.store_a, ticket.id, actor_user_id=seed.user_a)


async def test_waiting_list_excludes_completed(db_session: AsyncSession, seed: Seed) -> None:
    svc = _svc(db_session)
    keep = await svc.create(seed.store_a, name="留著", actor_user_id=seed.user_a)
    gone = await svc.create(seed.store_a, name="完成", actor_user_id=seed.user_a)
    await svc.complete(seed.store_a, gone.id, actor_user_id=seed.user_a)
    rows = await svc.list_tickets(seed.store_a, include_done=False)
    assert [r.id for r in rows] == [keep.id]


async def test_completed_ticket_is_still_retrievable(
    db_session: AsyncSession, seed: Seed
) -> None:
    """裁示「資料留著」的落點：沒有這個查詢，資料等於留了也找不到。"""
    svc = _svc(db_session)
    ticket = await svc.create(
        seed.store_a,
        name="陳小姐",
        link="https://example.com/form/123",
        note="帳篷兩頂",
        actor_user_id=seed.user_a,
    )
    await svc.complete(seed.store_a, ticket.id, actor_user_id=seed.user_a)
    rows = await svc.list_tickets(seed.store_a, include_done=True)
    found = next(r for r in rows if r.id == ticket.id)
    assert (found.link, found.note) == ("https://example.com/form/123", "帳篷兩頂")


async def test_unfinished_tickets_from_previous_days_stay_in_the_list(
    db_session: AsyncSession, seed: Seed
) -> None:
    """跨日未完成的不得憑空消失——那是客人真的還在等的單。"""
    svc = _svc(db_session)
    old = await svc.create(
        seed.store_a, name="昨天沒處理完", actor_user_id=seed.user_a, now=YESTERDAY
    )
    rows = await svc.list_tickets(seed.store_a, include_done=False)
    assert old.id in [r.id for r in rows]


async def test_foreign_key_error_is_not_swallowed_as_a_number_clash(
    db_session: AsyncSession, seed: Seed
) -> None:
    """**不得把所有 IntegrityError 都當成撞號**。

    配號撞號會重試，但外鍵不存在之類的錯誤必須原樣拋出——當成撞號重試只會
    把真正的錯誤重試三次再拋，或更糟：吞成看似成功（實作時真的踩過）。
    """
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        await _svc(db_session).create(
            seed.store_a, name="壞使用者", actor_user_id=999_999_999
        )


async def test_waiting_sorts_before_done_regardless_of_letters(
    db_session: AsyncSession, seed: Seed
) -> None:
    """待處理必須排在已完成之前——**不可依賴狀態字串的字母序**。

    原本用 `status.desc()` 讓 WAITING 排在 DONE 前面，那是靠 'W' > 'D' 的巧合；
    日後多一個狀態就會無聲跑掉。
    """
    svc = _svc(db_session)
    done = await svc.create(seed.store_a, name="先完成", actor_user_id=seed.user_a)
    await svc.complete(seed.store_a, done.id, actor_user_id=seed.user_a)
    waiting = await svc.create(seed.store_a, name="後取號但還在等", actor_user_id=seed.user_a)

    rows = await svc.list_tickets(seed.store_a, include_done=True)
    assert [r.id for r in rows] == [waiting.id, done.id]


async def test_list_respects_limit(db_session: AsyncSession, seed: Seed) -> None:
    """清單是舊的排前面：limit 太小會讓**剛取號的客人**從畫面上消失。"""
    svc = _svc(db_session)
    for i in range(5):
        await svc.create(seed.store_a, name=f"客{i}", actor_user_id=seed.user_a)
    assert len(await svc.list_tickets(seed.store_a, limit=3)) == 3
