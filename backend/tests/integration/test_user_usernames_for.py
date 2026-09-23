"""UserService.usernames_for：一批本店使用者的帳號名（收購紀錄顯示經手人用）。

一次查完、不逐人查；他店或不存在的 id 不回。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.store.models import Store
from app.modules.user.models import User
from app.modules.user.service import UserService
from app.shared.enums import UserRole


async def test_returns_only_this_stores_users(db_session: AsyncSession) -> None:
    mine, other = Store(name="本店"), Store(name="他店")
    db_session.add_all([mine, other])
    await db_session.flush()
    a = User(store_id=mine.id, username=f"阿明{mine.id}", password_hash="h", role=UserRole.CLERK)
    b = User(store_id=mine.id, username=f"小美{mine.id}", password_hash="h", role=UserRole.CLERK)
    c = User(store_id=other.id, username=f"他店{other.id}", password_hash="h", role=UserRole.CLERK)
    db_session.add_all([a, b, c])
    await db_session.flush()

    names = await UserService(db_session).usernames_for(mine.id, [a.id, b.id, a.id, c.id, 999999])

    assert names == {a.id: f"阿明{mine.id}", b.id: f"小美{mine.id}"}


async def test_empty_ids_skips_the_query(db_session: AsyncSession) -> None:
    assert await UserService(db_session).usernames_for(1, []) == {}
