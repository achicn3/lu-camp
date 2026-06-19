"""stocktake 資料存取層（唯一直接碰 stocktake ORM 的層）。

跨模組邊界：本層只碰 stocktakes / stocktake_lines；catalog 現量讀寫與 ADJUST 帳由
inventory service 處理（不直接碰 inventory 表）。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.stocktake.models import Stocktake, StocktakeLine


class StocktakeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_stocktake(self, stocktake: Stocktake) -> Stocktake:
        self._session.add(stocktake)
        await self._session.flush()
        return stocktake

    async def add_line(self, line: StocktakeLine) -> StocktakeLine:
        self._session.add(line)
        await self._session.flush()
        return line

    async def get_stocktake(self, store_id: int, stocktake_id: int) -> Stocktake | None:
        stmt = (
            select(Stocktake)
            .options(selectinload(Stocktake.lines))
            .where(Stocktake.id == stocktake_id, Stocktake.store_id == store_id)
        )
        result: Stocktake | None = await self._session.scalar(stmt)
        return result

    async def lock_stocktake(self, store_id: int, stocktake_id: int) -> Stocktake | None:
        """取盤點單並上行鎖（FOR UPDATE）+ 刷新已提交狀態；確認時防重複（僅一次）。"""
        stmt = (
            select(Stocktake)
            .options(selectinload(Stocktake.lines))
            .where(Stocktake.id == stocktake_id, Stocktake.store_id == store_id)
            .with_for_update(of=Stocktake)
            .execution_options(populate_existing=True)
        )
        result: Stocktake | None = await self._session.scalar(stmt)
        return result

    async def list_stocktakes(self, store_id: int, *, limit: int, offset: int) -> list[Stocktake]:
        stmt = (
            select(Stocktake)
            .options(selectinload(Stocktake.lines))
            .where(Stocktake.store_id == store_id)
            .order_by(Stocktake.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.scalars(stmt)).all())
