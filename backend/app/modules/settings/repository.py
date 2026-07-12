"""settings 資料存取層（唯一直接碰 ORM 的層）。"""

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.settings.models import PremiumRateHistory, StoreSettings

# 每店設定的交易級 advisory lock 命名空間（classid）：讓「結帳讀設定」與「PATCH 改設定」
# 序列化，即使設定列尚未存在也能鎖（避免 FOR UPDATE 需先物化列的插入競態）。
# 值任取、只需在本系統各 advisory lock 用途間唯一。
_SETTINGS_LOCK_CLASSID = 0x5E771234


class SettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def acquire_store_lock(self, store_id: int) -> None:
        """取得該店設定的交易級 advisory lock（pg_advisory_xact_lock，commit 時自動釋放）。

        create_sale（讀 einvoice_enabled 決策）與 update_settings（改該欄）都先取此鎖，
        使兩者對同店序列化：結帳的設定比對與其後的發票決策之間，設定不可被 PATCH 改掉
        （Codex 第二十三輪 TOCTOU）。單一鎖鍵、無 AB-BA 死鎖之虞。
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:classid, :store_id)").bindparams(
                bindparam("classid", _SETTINGS_LOCK_CLASSID),
                bindparam("store_id", store_id),
            )
        )

    async def get_by_store(self, store_id: int) -> StoreSettings | None:
        stmt = select(StoreSettings).where(StoreSettings.store_id == store_id)
        result: StoreSettings | None = await self._session.scalar(stmt)
        return result

    async def add(self, settings: StoreSettings) -> StoreSettings:
        self._session.add(settings)
        await self._session.flush()
        return settings

    async def add_history(self, row: PremiumRateHistory) -> PremiumRateHistory:
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_history(
        self, store_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[PremiumRateHistory]:
        stmt = (
            select(PremiumRateHistory)
            .where(PremiumRateHistory.store_id == store_id)
            .order_by(PremiumRateHistory.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.scalars(stmt)).all())
