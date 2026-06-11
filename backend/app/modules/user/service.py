"""user 業務邏輯：登入認證與 store-scoped 使用者查驗（§2 跨模組只經 service）。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.modules.user.models import User
from app.modules.user.repository import UserRepository

# 帳號不存在時仍對此假雜湊跑一次 argon2 驗證，使「不存在」與「密碼錯誤」
# 的回應時間相近，降低時序式使用者列舉（與回應訊息一致化互補）。
_DUMMY_HASH = hash_password("timing-equalizer-dummy")


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = UserRepository(session)

    async def get_user_in_store(self, store_id: int, user_id: int) -> User | None:
        """取得屬於該 store 的使用者；不屬於或不存在則回 None。"""
        return await self._repo.get_in_store(store_id, user_id)

    async def authenticate(self, username: str, password: str) -> User | None:
        """驗證帳密；帳號不存在／密碼錯誤／已停用一律回 None（呼叫端統一 401）。"""
        user = await self._repo.get_by_username(username)
        if user is None:
            verify_password(password, _DUMMY_HASH)  # 等時化，結果必為 False
            return None
        if not verify_password(password, user.password_hash):
            return None
        if not user.is_active:
            return None
        return user
