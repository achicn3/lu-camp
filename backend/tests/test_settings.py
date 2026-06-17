"""T8 — settings 領域層：每店單列具型別設定（get-or-create、更新、稽核）。"""

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditLog
from app.modules.settings.defaults import (
    DEFAULT_ALLOW_CLERK_MANAGE_CATEGORIES,
    DEFAULT_COMMISSION_PCT,
    DEFAULT_EINVOICE_ENABLED,
    DEFAULT_MARGIN_PCT,
    DEFAULT_TAX_RATE,
)
from app.modules.settings.models import StoreSettings
from app.modules.settings.schemas import SettingsUpdateRequest
from app.modules.settings.service import StoreSettingsService
from app.modules.store.models import Store
from app.modules.user.models import User
from app.shared.enums import UserRole


async def _seed_store(session: AsyncSession) -> int:
    store = Store(name="門市")
    session.add(store)
    await session.flush()
    return store.id


async def _seed_store_and_user(session: AsyncSession) -> tuple[int, int]:
    store_id = await _seed_store(session)
    mgr = User(store_id=store_id, username="mgr", password_hash="h", role=UserRole.MANAGER)
    session.add(mgr)
    await session.flush()
    return store_id, mgr.id


async def test_get_effective_returns_defaults_without_persisting(db_session: AsyncSession) -> None:
    store_id = await _seed_store(db_session)
    svc = StoreSettingsService(db_session)
    s = await svc.get_effective_settings(store_id)
    assert s.einvoice_enabled is DEFAULT_EINVOICE_ENABLED
    assert s.tax_rate == DEFAULT_TAX_RATE
    assert s.default_commission_pct == DEFAULT_COMMISSION_PCT
    assert s.default_margin_pct == DEFAULT_MARGIN_PCT
    assert s.allow_clerk_manage_categories is DEFAULT_ALLOW_CLERK_MANAGE_CATEGORIES
    # 未持久化：DB 尚無該店 settings 列。
    assert await svc.get_persisted(store_id) is None


async def test_update_creates_row_and_applies_patch(db_session: AsyncSession) -> None:
    store_id, user_id = await _seed_store_and_user(db_session)
    svc = StoreSettingsService(db_session)
    updated = await svc.update_settings(
        store_id,
        actor_user_id=user_id,
        patch=SettingsUpdateRequest(
            einvoice_enabled=True,
            default_commission_pct=40,
            allow_clerk_manage_categories=True,
        ),
    )
    assert updated.einvoice_enabled is True
    assert updated.default_commission_pct == 40
    assert updated.allow_clerk_manage_categories is True
    # 未提供的欄位維持預設。
    assert updated.tax_rate == DEFAULT_TAX_RATE
    assert updated.default_margin_pct == DEFAULT_MARGIN_PCT
    # 已持久化。
    persisted = await svc.get_persisted(store_id)
    assert persisted is not None
    assert persisted.einvoice_enabled is True


async def test_update_is_idempotent_on_existing_row(db_session: AsyncSession) -> None:
    store_id, user_id = await _seed_store_and_user(db_session)
    svc = StoreSettingsService(db_session)
    await svc.update_settings(
        store_id, actor_user_id=user_id, patch=SettingsUpdateRequest(default_margin_pct=30)
    )
    again = await svc.update_settings(
        store_id, actor_user_id=user_id, patch=SettingsUpdateRequest(tax_rate=Decimal("0.00"))
    )
    assert again.default_margin_pct == 30  # 先前的變更保留
    assert again.tax_rate == Decimal("0.00")
    # 不重複建列。
    rows = (
        await db_session.scalars(select(StoreSettings).where(StoreSettings.store_id == store_id))
    ).all()
    assert len(rows) == 1


async def test_update_writes_audit(db_session: AsyncSession) -> None:
    store_id, user_id = await _seed_store_and_user(db_session)
    svc = StoreSettingsService(db_session)
    await svc.update_settings(
        store_id, actor_user_id=user_id, patch=SettingsUpdateRequest(einvoice_enabled=True)
    )
    audits = (await db_session.scalars(select(AuditLog))).all()
    settings_audits = [a for a in audits if a.action == "UPDATE_SETTINGS"]
    assert len(settings_audits) == 1
    a = settings_audits[0]
    assert a.store_id == store_id
    assert a.actor_user_id == user_id
    assert a.entity_type == "settings"
    # after 反映變更後的值。
    assert a.after is not None and a.after.get("einvoice_enabled") is True


@pytest.mark.parametrize(
    "patch_kwargs",
    [
        {"tax_rate": Decimal("1")},
        {"tax_rate": Decimal("-0.01")},
        {"default_commission_pct": 101},
        {"default_commission_pct": -1},
        {"default_margin_pct": 100},
        {"default_margin_pct": -1},
    ],
)
def test_update_request_rejects_out_of_range(patch_kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SettingsUpdateRequest(**patch_kwargs)
