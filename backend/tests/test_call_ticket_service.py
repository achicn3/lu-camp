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


async def test_waiting_list_shows_today_only(db_session: AsyncSession, seed: Seed) -> None:
    """**候位清單只看今天**（店主裁示 2026-08-19）。

    「昨天未完成從今天的角度根本不重要」——跨日的不再佔用今天的清單。
    """
    svc = _svc(db_session)
    stale = await svc.create(
        seed.store_a, name="昨天沒處理完", actor_user_id=seed.user_a, now=YESTERDAY
    )
    fresh = await svc.create(seed.store_a, name="今天", actor_user_id=seed.user_a, now=TODAY)

    rows = await svc.list_tickets(seed.store_a, include_done=False, now=TODAY)
    assert [r.id for r in rows] == [fresh.id]
    assert stale.id not in [r.id for r in rows]


async def test_stale_waiting_is_still_findable_in_history(
    db_session: AsyncSession, seed: Seed
) -> None:
    """**資料不刪**：跨日未完成的離開候位清單後，歷史檢視是唯一找得回它的地方。

    否則客人先前填的那份表單連結就永遠消失了。
    """
    svc = _svc(db_session)
    stale = await svc.create(
        seed.store_a,
        name="昨天沒處理完",
        link="https://example.com/form/stale",
        actor_user_id=seed.user_a,
        now=YESTERDAY,
    )
    rows = await svc.list_tickets(seed.store_a, include_done=True, now=TODAY)
    found = next(r for r in rows if r.id == stale.id)
    assert found.status is CallTicketStatus.WAITING
    assert found.link == "https://example.com/form/stale"


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


async def test_waiting_is_oldest_first_but_done_is_newest_first(
    db_session: AsyncSession, seed: Seed
) -> None:
    """兩群的排序方向**相反**，且必須真的相反。

    待處理＝排隊，先來先服務（舊的先）；已完成＝回頭找先前的表單連結，
    要找的幾乎都是最近那筆（新的先）。若已完成也跟著遞增，歷史累積後撈到的會是
    幾百天前的單、剛完成的被擠出 limit——「顯示已完成」就形同失效。
    """
    svc = _svc(db_session)
    old_done = await svc.create(
        seed.store_a, name="很久以前完成", actor_user_id=seed.user_a, now=YESTERDAY
    )
    await svc.complete(seed.store_a, old_done.id, actor_user_id=seed.user_a)
    new_done = await svc.create(
        seed.store_a, name="剛剛完成", actor_user_id=seed.user_a, now=TODAY
    )
    await svc.complete(seed.store_a, new_done.id, actor_user_id=seed.user_a)
    old_wait = await svc.create(
        seed.store_a, name="等最久", actor_user_id=seed.user_a, now=YESTERDAY
    )
    new_wait = await svc.create(
        seed.store_a, name="剛取號", actor_user_id=seed.user_a, now=TODAY
    )

    rows = await svc.list_tickets(seed.store_a, include_done=True, now=TODAY)
    # 候位只剩今天那筆；昨天未完成的落到歷史區（與已完成一起，最近的先）
    assert rows[0].id == new_wait.id
    assert [r.id for r in rows[1:]] == [new_done.id, old_wait.id, old_done.id]


async def test_done_is_not_starved_by_the_limit(
    db_session: AsyncSession, seed: Seed
) -> None:
    """limit 用在**合併後**的清單：候位吃掉全部額度時，已完成就一筆都不撈。"""
    svc = _svc(db_session)
    done = await svc.create(seed.store_a, name="已完成", actor_user_id=seed.user_a)
    await svc.complete(seed.store_a, done.id, actor_user_id=seed.user_a)
    for i in range(3):
        await svc.create(seed.store_a, name=f"候位{i}", actor_user_id=seed.user_a)

    rows = await svc.list_tickets(seed.store_a, include_done=True, limit=2)
    assert len(rows) == 2
    assert all(r.status is CallTicketStatus.WAITING for r in rows)


async def test_list_respects_limit(db_session: AsyncSession, seed: Seed) -> None:
    """清單是舊的排前面：limit 太小會讓**剛取號的客人**從畫面上消失。"""
    svc = _svc(db_session)
    for i in range(5):
        await svc.create(seed.store_a, name=f"客{i}", actor_user_id=seed.user_a)
    assert len(await svc.list_tickets(seed.store_a, limit=3)) == 3


async def test_number_clash_is_retried_with_a_fresh_number(
    db_session: AsyncSession, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**撞號要能重試成功**——這是並發配號的核心守衛，先前零覆蓋。

    模擬「讀到 max 之後、寫入之前被別人插隊」：第一次配號故意回一個已被用掉的號碼，
    唯一索引擋下 → savepoint 退掉 → 重讀 max 再寫。最終必須拿到正確的下一號，
    且**呼叫端先前完成的工作不得被一併回滾**。
    """
    from app.modules.callticket.repository import CallTicketRepository

    svc = _svc(db_session)
    first = await svc.create(seed.store_a, name="已存在", actor_user_id=seed.user_a)
    assert first.ticket_no == 1

    real = CallTicketRepository.next_ticket_no
    calls = {"n": 0}

    async def _clash_once(self: CallTicketRepository, store_id: int, ticket_date: object) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            return 1  # 撞上 first
        return await real(self, store_id, ticket_date)  # type: ignore[arg-type]

    monkeypatch.setattr(CallTicketRepository, "next_ticket_no", _clash_once)
    second = await svc.create(seed.store_a, name="撞號後重取", actor_user_id=seed.user_a)

    assert calls["n"] == 2, "第一次應撞號並重試一次"
    assert second.ticket_no == 2
    # 先前那筆仍在（savepoint 只退掉失敗的那次新增，沒動整個交易）
    rows = await svc.list_tickets(seed.store_a, include_done=True)
    assert {r.id for r in rows} == {first.id, second.id}


async def test_persistent_clash_eventually_raises(
    db_session: AsyncSession, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一直撞號**不可無限迴圈**：重試上限用完就原樣拋出。"""
    from sqlalchemy.exc import IntegrityError

    from app.modules.callticket.repository import CallTicketRepository

    svc = _svc(db_session)
    await svc.create(seed.store_a, name="佔號", actor_user_id=seed.user_a)

    async def _always_clash(
        self: CallTicketRepository, store_id: int, ticket_date: object
    ) -> int:
        return 1

    monkeypatch.setattr(CallTicketRepository, "next_ticket_no", _always_clash)
    with pytest.raises(IntegrityError):
        await svc.create(seed.store_a, name="永遠撞", actor_user_id=seed.user_a)
