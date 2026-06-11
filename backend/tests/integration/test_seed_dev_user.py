"""開發用使用者 seed 腳本測試：env 組值（密碼必填）、upsert 建立/更新、不洩漏密碼。"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.modules.store.models import Store
from app.modules.user.models import User
from app.scripts.seed_dev_user import DevUserSeed, seed_from_env, upsert_dev_user
from app.shared.enums import UserRole


def test_seed_from_env_requires_password() -> None:
    """密碼無預設：未設即報錯退出（開發密碼也不入 repo）。"""
    with pytest.raises(SystemExit):
        seed_from_env({})
    with pytest.raises(SystemExit):
        seed_from_env({"SEED_USER_PASSWORD": "   "})


def test_seed_from_env_defaults_and_overrides() -> None:
    seed = seed_from_env({"SEED_USER_PASSWORD": "pw-abc"})
    assert seed.username == "dev-manager"
    assert seed.role is UserRole.MANAGER
    assert seed.store_id == 1
    custom = seed_from_env(
        {
            "SEED_USER_PASSWORD": "pw-abc",
            "SEED_USER_USERNAME": "clerk-dev",
            "SEED_USER_ROLE": "CLERK",
            "SEED_USER_STORE_ID": "2",
        }
    )
    assert custom.username == "clerk-dev"
    assert custom.role is UserRole.CLERK
    assert custom.store_id == 2


async def _seed_store(session: AsyncSession) -> int:
    store = Store(name="seed 用門市")
    session.add(store)
    await session.flush()
    return store.id


async def test_upsert_creates_then_updates(db_session: AsyncSession) -> None:
    store_id = await _seed_store(db_session)
    created = await upsert_dev_user(
        db_session,
        DevUserSeed(username="dev-x", password="first-pw", role=UserRole.CLERK, store_id=store_id),
    )
    assert created.id is not None
    assert verify_password("first-pw", created.password_hash)
    assert created.role is UserRole.CLERK

    created.is_active = False  # 模擬被停用後重 seed 應重新啟用
    updated = await upsert_dev_user(
        db_session,
        DevUserSeed(
            username="dev-x", password="second-pw", role=UserRole.MANAGER, store_id=store_id
        ),
    )
    assert updated.id == created.id  # 同 username upsert 同一列
    assert verify_password("second-pw", updated.password_hash)
    assert not verify_password("first-pw", updated.password_hash)
    assert updated.role is UserRole.MANAGER
    assert updated.is_active is True
    count = await db_session.scalar(
        select(func.count()).select_from(User).where(User.username == "dev-x")
    )
    assert count == 1  # upsert 不重複建列
