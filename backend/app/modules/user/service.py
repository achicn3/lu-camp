"""user 業務邏輯：供他模組以 service 介面做 store-scoped 使用者查驗（§2 跨模組只經 service）。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.user.models import User
from app.modules.user.repository import UserRepository


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = UserRepository(session)

    async def get_user_in_store(self, store_id: int, user_id: int) -> User | None:
        """取得屬於該 store 的使用者；不屬於或不存在則回 None。"""
        return await self._repo.get_in_store(store_id, user_id)
