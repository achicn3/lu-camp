"""sales 資料存取層（唯一直接碰 ORM 的層）。"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.sales.models import Sale, SaleLine


class SalesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_sale(self, sale: Sale) -> Sale:
        self._session.add(sale)
        await self._session.flush()
        return sale

    async def add_line(self, line: SaleLine) -> SaleLine:
        self._session.add(line)
        await self._session.flush()
        return line

    async def get_sale(self, store_id: int, sale_id: int) -> Sale | None:
        stmt = select(Sale).where(Sale.id == sale_id, Sale.store_id == store_id)
        result: Sale | None = await self._session.scalar(stmt)
        return result

    async def lock_sale(self, store_id: int, sale_id: int) -> Sale | None:
        """對 sale 列上 row lock 並刷新到已提交狀態（供作廢前序列化，擋併發重複作廢）。"""
        stmt = (
            select(Sale)
            .where(Sale.id == sale_id, Sale.store_id == store_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result: Sale | None = await self._session.scalar(stmt)
        return result

    async def get_by_idempotency_key(self, store_id: int, key: str) -> Sale | None:
        stmt = select(Sale).where(Sale.store_id == store_id, Sale.idempotency_key == key)
        result: Sale | None = await self._session.scalar(stmt)
        return result

    async def list_sales(
        self,
        store_id: int,
        *,
        date_from: datetime | None,
        date_to: datetime | None,
        limit: int,
        offset: int,
    ) -> list[Sale]:
        stmt = select(Sale).where(Sale.store_id == store_id)
        if date_from is not None:
            stmt = stmt.where(Sale.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(Sale.created_at <= date_to)
        stmt = stmt.order_by(Sale.id.desc()).limit(limit).offset(offset)
        result = await self._session.scalars(stmt)
        return list(result)

    async def list_lines(self, sale_id: int) -> list[SaleLine]:
        stmt = select(SaleLine).where(SaleLine.sale_id == sale_id).order_by(SaleLine.id)
        result = await self._session.scalars(stmt)
        return list(result)
