"""sales 資料存取層（唯一直接碰 ORM 的層）。"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.sales.models import Sale, SaleLine, SaleTender


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

    async def add_tender(self, tender: SaleTender) -> SaleTender:
        self._session.add(tender)
        await self._session.flush()
        return tender

    async def list_tenders(self, sale_id: int) -> list[SaleTender]:
        stmt = select(SaleTender).where(SaleTender.sale_id == sale_id).order_by(SaleTender.id)
        result = await self._session.scalars(stmt)
        return list(result)

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

    async def list_sales_by_buyer(
        self,
        store_id: int,
        contact_id: int,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        limit: int,
        offset: int,
    ) -> list[Sale]:
        """某買方的銷售（會員消費紀錄；store 範圍、可選日期區間、新到舊、分頁）。

        日期過濾在分頁**之前**套用（與 list_sales 一致）——否則分頁先作用於未過濾的
        全部消費史，落在區間外的新單會吃掉名額、回傳短頁/空頁（Codex review P2）。
        """
        stmt = select(Sale).where(
            Sale.store_id == store_id, Sale.buyer_contact_id == contact_id
        )
        if date_from is not None:
            stmt = stmt.where(Sale.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(Sale.created_at <= date_to)
        stmt = stmt.order_by(Sale.id.desc()).limit(limit).offset(offset)
        result = await self._session.scalars(stmt)
        return list(result)

    async def count_sales_by_buyer(self, store_id: int, contact_id: int) -> int:
        """某買方的銷售總筆數（會員中心 overview）。"""
        stmt = (
            select(func.count())
            .select_from(Sale)
            .where(Sale.store_id == store_id, Sale.buyer_contact_id == contact_id)
        )
        return int(await self._session.scalar(stmt) or 0)

    async def count_lines_for_sales(self, sale_ids: list[int]) -> dict[int, int]:
        """各銷售單的明細行數（單一 grouped 查詢，避免 N+1；空 → {}）。"""
        if not sale_ids:
            return {}
        stmt = (
            select(SaleLine.sale_id, func.count())
            .where(SaleLine.sale_id.in_(sale_ids))
            .group_by(SaleLine.sale_id)
        )
        rows = await self._session.execute(stmt)
        return {sale_id: count for sale_id, count in rows.all()}
