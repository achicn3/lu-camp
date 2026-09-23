"""user 資料存取層（唯一直接碰 ORM 的層）。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_in_store(self, store_id: int, user_id: int) -> User | None:
        stmt = select(User).where(User.id == user_id, User.store_id == store_id)
        result: User | None = await self._session.scalar(stmt)
        return result

    async def usernames_in_store(self, store_id: int, user_ids: list[int]) -> dict[int, str]:
        """一批本店使用者的帳號名（一次查完）；他店或不存在的 id 不回。"""
        if not user_ids:
            return {}
        stmt = select(User.id, User.username).where(
            User.store_id == store_id, User.id.in_(set(user_ids))
        )
        return {row.id: row.username for row in await self._session.execute(stmt)}

    async def get_by_username(self, username: str) -> User | None:
        """以帳號查使用者（username 全域唯一，登入用、不分店）。"""
        stmt = select(User).where(User.username == username)
        result: User | None = await self._session.scalar(stmt)
        return result
