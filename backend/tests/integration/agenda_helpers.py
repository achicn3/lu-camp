"""行事曆（docs/99）測試共用：建店、管理者、店員與 token。"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encode_access_token
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole


@dataclass(frozen=True)
class SeededStore:
    store_id: int
    manager_id: int
    clerk_id: int
    manager_token: str
    clerk_token: str


async def seed_store(session: AsyncSession, *, name: str = "門市") -> SeededStore:
    """建一間店＋一位管理者＋一位店員（帳號名稱以店名區分，可在同一測試建兩間店）。"""
    store = Store(name=name)
    session.add(store)
    await session.flush()
    manager = User(
        store_id=store.id, username=f"{name}-mgr", password_hash="h", role=UserRole.MANAGER
    )
    clerk = User(store_id=store.id, username=f"{name}-clk", password_hash="h", role=UserRole.CLERK)
    session.add_all([manager, clerk])
    # commit（在測試的外層交易裡只是放掉 savepoint）：API 錯誤路徑會 rollback，
    # 沒 commit 的話剛建的帳號會被一起退掉，下一個請求就變 401。
    await session.commit()
    return SeededStore(
        store_id=store.id,
        manager_id=manager.id,
        clerk_id=clerk.id,
        manager_token=encode_access_token(user_id=manager.id, role="MANAGER", store_id=store.id),
        clerk_token=encode_access_token(user_id=clerk.id, role="CLERK", store_id=store.id),
    )


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
