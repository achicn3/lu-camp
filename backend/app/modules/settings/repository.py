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
        """取得該店設定的交易級**互斥** advisory lock（pg_advisory_xact_lock，commit 釋放）。

        update_settings（改 einvoice_enabled 等）用；與結帳的共享鎖形成 reader/writer：
        PATCH（writer）需等所有在途結帳（reader）結束、並擋住新結帳，直到本交易 commit
        （Codex 第廿三/廿四輪 TOCTOU）。單一鎖鍵、無 AB-BA 死鎖之虞。
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:classid, :store_id)").bindparams(
                bindparam("classid", _SETTINGS_LOCK_CLASSID),
                bindparam("store_id", store_id),
            )
        )

    async def acquire_store_lock_shared(self, store_id: int) -> None:
        """取得該店設定的交易級**共享** advisory lock（pg_advisory_xact_lock_shared）。

        結帳（reader：讀 einvoice_enabled 決策）用——多筆結帳可同時持有、彼此不阻塞；
        僅與 update_settings 的互斥鎖（writer）互斥，使「讀設定→發票決策→commit」期間
        設定不可被 PATCH 改掉，又不犧牲並發結帳吞吐。
        """
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock_shared(:classid, :store_id)").bindparams(
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
