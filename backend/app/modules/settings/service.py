"""settings 業務邏輯：每店單列設定的取得（get-or-create）與更新。

GET 端的有效值：若該店尚未建列，回傳以 defaults 組成的「暫態」設定（不寫 DB）；
PATCH 端：首次更新才建列（get-or-create），套用有帶入的欄位並寫稽核（config 變更可追溯）。
"""

from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit_log
from app.modules.settings.defaults import (
    DEFAULT_ALLOW_CLERK_MANAGE_CATEGORIES,
    DEFAULT_COMMISSION_PCT,
    DEFAULT_EINVOICE_ENABLED,
    DEFAULT_MARGIN_PCT,
    DEFAULT_MONTHLY_FIXED_CASH_OUTFLOW,
    DEFAULT_PREMIUM_RATE,
    DEFAULT_PREMIUM_RATE_MAX,
    DEFAULT_PREMIUM_RATE_MIN,
    DEFAULT_STORE_CREDIT_ENGINE_PARAMS,
    DEFAULT_TAX_RATE,
)
from app.modules.settings.models import PremiumRateHistory, StoreSettings
from app.modules.settings.repository import SettingsRepository
from app.modules.settings.schemas import SettingsUpdateRequest
from app.shared.exceptions import InvalidPremiumRate


def _jsonable(value: Any) -> Any:
    """Decimal 轉字串以便寫入 JSON 稽核欄（避免序列化失敗、保留精度）。"""
    return str(value) if isinstance(value, Decimal) else value


def _new_settings(store_id: int) -> StoreSettings:
    """以 defaults 組一列設定（含 SC-5 新欄位），供暫態有效值與首次建列共用。"""
    return StoreSettings(
        store_id=store_id,
        einvoice_enabled=DEFAULT_EINVOICE_ENABLED,
        tax_rate=DEFAULT_TAX_RATE,
        default_commission_pct=DEFAULT_COMMISSION_PCT,
        default_margin_pct=DEFAULT_MARGIN_PCT,
        allow_clerk_manage_categories=DEFAULT_ALLOW_CLERK_MANAGE_CATEGORIES,
        premium_rate=DEFAULT_PREMIUM_RATE,
        premium_rate_min=DEFAULT_PREMIUM_RATE_MIN,
        premium_rate_max=DEFAULT_PREMIUM_RATE_MAX,
        monthly_fixed_cash_outflow=Decimal(DEFAULT_MONTHLY_FIXED_CASH_OUTFLOW),
        store_credit_engine_params=dict(DEFAULT_STORE_CREDIT_ENGINE_PARAMS),
    )


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
        return _new_settings(store_id)

    async def update_settings(
        self, store_id: int, *, actor_user_id: int | None, patch: SettingsUpdateRequest
    ) -> StoreSettings:
        """套用 PATCH（僅有帶入的欄位）；首次更新才建列。溢價率變更寫 premium_rate_history。

        溢價率政策（docs/16 §6.1）：min ≤ max，且 premium_rate ∈ [min, max]（界線可同 PATCH
        一併更動，套用後再驗）。premium_rate 實際變動時，寫一筆 premium_rate_history（old→new、
        actor、事由）並寫 UPDATE_SETTINGS 稽核。
        """
        settings = await self._repo.get_by_store(store_id)
        if settings is None:
            settings = await self._repo.add(_new_settings(store_id))

        # exclude_none：明確傳 null 視為「不更動」（這些設定欄皆不可為 NULL；Codex P2 防 500）。
        changes = patch.model_dump(exclude_unset=True, exclude_none=True)
        reason = changes.pop("premium_change_reason", None)  # 非設定欄，僅供 history 留痕
        old_premium = settings.premium_rate
        before = {k: _jsonable(getattr(settings, k)) for k in changes}
        for key, value in changes.items():
            setattr(settings, key, value)

        if settings.premium_rate_min > settings.premium_rate_max:
            raise InvalidPremiumRate("溢價率下限不可大於上限")
        if not (settings.premium_rate_min <= settings.premium_rate <= settings.premium_rate_max):
            raise InvalidPremiumRate(
                f"溢價率 {settings.premium_rate} 必須在 "
                f"[{settings.premium_rate_min}, {settings.premium_rate_max}] 之間"
            )
        await self._session.flush()

        # 溢價率實際變動且有操作者時寫留痕（changed_by FK 必填；router 一律帶 actor，
        # 內部/測試以 actor=None 直呼者僅變更不留痕）。
        if (
            "premium_rate" in changes
            and settings.premium_rate != old_premium
            and actor_user_id is not None
        ):
            await self._repo.add_history(
                PremiumRateHistory(
                    store_id=store_id,
                    changed_by=actor_user_id,
                    old_rate=old_premium,
                    new_rate=settings.premium_rate,
                    suggested_rate_at_change=None,  # SC-5b 引擎上線後帶入當下建議值
                    reason=reason,
                )
            )

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

    async def list_premium_history(
        self, store_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[PremiumRateHistory]:
        return await self._repo.list_history(store_id, limit=limit, offset=offset)
