"""openingcheck 資料存取：自訂項目與每日狀態（唯一能碰 DB 的層）。"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.openingcheck.models import OpeningCheck, OpeningCheckItem


class OpeningCheckRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_items(self, store_id: int) -> list[OpeningCheckItem]:
        """未封存的自訂項目，依排序與建立順序。"""
        rows = await self._session.scalars(
            select(OpeningCheckItem)
            .where(
                OpeningCheckItem.store_id == store_id,
                OpeningCheckItem.archived_at.is_(None),
            )
            .order_by(OpeningCheckItem.sort_order, OpeningCheckItem.id)
        )
        return list(rows.all())

    async def get_item(self, store_id: int, item_id: int) -> OpeningCheckItem | None:
        item: OpeningCheckItem | None = await self._session.scalar(
            select(OpeningCheckItem).where(
                OpeningCheckItem.store_id == store_id,
                OpeningCheckItem.id == item_id,
                OpeningCheckItem.archived_at.is_(None),
            )
        )
        return item

    async def add_item(self, item: OpeningCheckItem) -> OpeningCheckItem:
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_check(
        self, store_id: int, business_date: date, *, for_update: bool = False
    ) -> OpeningCheck | None:
        """取某日狀態列。for_update=True 時上 row lock，供打勾/略過的讀改寫序列化。"""
        stmt = select(OpeningCheck).where(
            OpeningCheck.store_id == store_id,
            OpeningCheck.business_date == business_date,
        )
        if for_update:
            stmt = stmt.with_for_update()
        check: OpeningCheck | None = await self._session.scalar(stmt)
        return check

    async def add_check(self, check: OpeningCheck) -> OpeningCheck:
        self._session.add(check)
        await self._session.flush()
        return check
