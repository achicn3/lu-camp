"""settings 業務邏輯：每店單列設定的取得（get-or-create）與更新。

GET 端的有效值：若該店尚未建列，回傳以 defaults 組成的「暫態」設定（不寫 DB）；
PATCH 端：首次更新才建列（get-or-create），套用有帶入的欄位並寫稽核（config 變更可追溯）。
"""

from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.settings.defaults import (
    DEFAULT_COMMISSION_PCT,
    DEFAULT_EINVOICE_ENABLED,
    DEFAULT_MARGIN_PCT,
    DEFAULT_TAX_RATE,
)
from app.modules.settings.models import StoreSettings
from app.modules.settings.repository import SettingsRepository
from app.modules.settings.schemas import SettingsUpdateRequest


def _jsonable(value: Any) -> Any:
    """Decimal 轉字串以便寫入 JSON 稽核欄（避免序列化失敗、保留精度）。"""
    return str(value) if isinstance(value, Decimal) else value


class StoreSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = SettingsRepository(session)

    async def get_persisted(self, store_id: int) -> StoreSettings | None:
        """回傳已持久化的設定列（無則 None）。"""
        return await self._repo.get_by_store(store_id)

    async def get_effective_settings(self, store_id: int) -> StoreSettings:
        """回傳該店有效設定：已建列則回該列，否則回 defaults 組成的暫態設定（不寫 DB）。"""
        existing = await self._repo.get_by_store(store_id)
        if existing is not None:
            return existing
        return StoreSettings(
            store_id=store_id,
            einvoice_enabled=DEFAULT_EINVOICE_ENABLED,
            tax_rate=DEFAULT_TAX_RATE,
            default_commission_pct=DEFAULT_COMMISSION_PCT,
            default_margin_pct=DEFAULT_MARGIN_PCT,
        )

    async def update_settings(
        self, store_id: int, *, actor_user_id: int | None, patch: SettingsUpdateRequest
    ) -> StoreSettings:
        """套用 PATCH（僅有帶入的欄位）；首次更新才建列。寫 UPDATE_SETTINGS 稽核。"""
        settings = await self._repo.get_by_store(store_id)
        if settings is None:
            settings = await self._repo.add(
                StoreSettings(
                    store_id=store_id,
                    einvoice_enabled=DEFAULT_EINVOICE_ENABLED,
                    tax_rate=DEFAULT_TAX_RATE,
                    default_commission_pct=DEFAULT_COMMISSION_PCT,
                    default_margin_pct=DEFAULT_MARGIN_PCT,
                )
            )

        changes = patch.model_dump(exclude_unset=True)
        before = {k: _jsonable(getattr(settings, k)) for k in changes}
        for key, value in changes.items():
            setattr(settings, key, value)
        await self._session.flush()

        await write_audit_log(
            self._session,
            store_id=store_id,
            actor_user_id=actor_user_id,
            action="UPDATE_SETTINGS",
            entity_type="settings",
            entity_id=str(store_id),
            before=before,
            after={k: _jsonable(getattr(settings, k)) for k in changes},
        )
        return settings
