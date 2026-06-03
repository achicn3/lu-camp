"""core/audit.py — append-only 稽核寫入，且不記錄 PII 明文。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog, write_audit_log
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole


async def _make_store_user(session: AsyncSession) -> tuple[int, int]:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    user = User(store_id=store.id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    session.add(user)
    await session.flush()
    return store.id, user.id


async def test_write_audit_log_inserts_row(db_session: AsyncSession) -> None:
    store_id, user_id = await _make_store_user(db_session)
    entry = await write_audit_log(
        db_session,
        store_id=store_id,
        actor_user_id=user_id,
        action="UPDATE_PRICE",
        entity_type="serialized_item",
        entity_id="123",
        before={"price": 100},
        after={"price": 120},
    )

    assert entry.id is not None
    assert entry.created_at is not None

    fetched = (
        await db_session.execute(select(AuditLog).where(AuditLog.id == entry.id))
    ).scalar_one()
    assert fetched.action == "UPDATE_PRICE"
    assert fetched.before == {"price": 100}
    assert fetched.after == {"price": 120}


async def test_audit_is_append_only_history(db_session: AsyncSession) -> None:
    store_id, user_id = await _make_store_user(db_session)
    await write_audit_log(
        db_session,
        store_id=store_id,
        actor_user_id=user_id,
        action="A",
        entity_type="x",
        entity_id="1",
    )
    await write_audit_log(
        db_session,
        store_id=store_id,
        actor_user_id=user_id,
        action="B",
        entity_type="x",
        entity_id="1",
    )
    count = (
        await db_session.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.store_id == store_id)
        )
    ).scalar_one()
    assert count == 2  # 兩筆各自留存，非覆寫


async def test_audit_redacts_pii_plaintext(db_session: AsyncSession) -> None:
    store_id, user_id = await _make_store_user(db_session)
    entry = await write_audit_log(
        db_session,
        store_id=store_id,
        actor_user_id=user_id,
        action="VIEW_PII",
        entity_type="contact",
        entity_id="1",
        before={"national_id": "A123456789", "name": "王小明"},
        is_sensitive=True,
    )

    assert entry.before is not None
    assert entry.before["national_id"] == "***REDACTED***"  # PII 明文被遮罩
    assert entry.before["name"] == "王小明"  # 非敏感欄位保留

    fetched = (
        await db_session.execute(select(AuditLog).where(AuditLog.id == entry.id))
    ).scalar_one()
    assert "A123456789" not in str(fetched.before)  # DB 內不含 national_id 明文
    assert fetched.is_sensitive is True
