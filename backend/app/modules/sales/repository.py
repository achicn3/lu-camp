"""sales 資料存取層（唯一直接碰 ORM 的層）。

T11 為領域層，僅需新增 sale / sale_line；查詢端（get/list）待 T12 接 API 時再加。
"""

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
