"""store / user 基礎模型：建立、查回、FK / enum / 預設值 / 時戳。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole


async def test_create_store_sets_id_and_timestamps(db_session: AsyncSession) -> None:
    store = Store(
        name="門市A",
        tax_id="12345678",
        invoice_track_info="AB",
        address="台北市…",
    )
    db_session.add(store)
    await db_session.flush()

    assert store.id is not None
    assert store.created_at is not None
    assert store.updated_at is not None


async def test_create_user_with_store_fk_and_role(db_session: AsyncSession) -> None:
    store = Store(name="門市B")
    db_session.add(store)
    await db_session.flush()

    user = User(
        store_id=store.id,
        username="clerk1",
        password_hash="hashed",
        role=UserRole.CLERK,
    )
    db_session.add(user)
    await db_session.flush()

    assert user.id is not None
    assert user.is_active is True  # 預設啟用
    assert user.role is UserRole.CLERK

    fetched = (await db_session.execute(select(User).where(User.id == user.id))).scalar_one()
    assert fetched.store_id == store.id
    assert fetched.role is UserRole.CLERK
