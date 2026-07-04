"""returns 資料存取層。"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.returns.models import CustomerReturn, ReturnLine


class ReturnsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_return(self, customer_return: CustomerReturn) -> CustomerReturn:
        self._session.add(customer_return)
        await self._session.flush()
        return customer_return

    async def add_line(self, line: ReturnLine) -> ReturnLine:
        self._session.add(line)
        await self._session.flush()
        return line

    async def has_returns_for_sale(self, store_id: int, sale_id: int) -> bool:
        """該銷售是否已有任何退貨單（供作廢前置檢查：已退貨者不可作廢）。"""
        stmt = select(
            select(CustomerReturn.id)
            .where(CustomerReturn.store_id == store_id, CustomerReturn.sale_id == sale_id)
            .exists()
        )
        return bool(await self._session.scalar(stmt))

    async def get_return(self, store_id: int, return_id: int) -> CustomerReturn | None:
        stmt = select(CustomerReturn).where(
            CustomerReturn.id == return_id,
            CustomerReturn.store_id == store_id,
        )
        result: CustomerReturn | None = await self._session.scalar(stmt)
        return result

    async def list_returns_for_sale(self, store_id: int, sale_id: int) -> list[CustomerReturn]:
        """某銷售的所有退貨單（發票核可後補開折讓用）。"""
        stmt = (
            select(CustomerReturn)
            .where(CustomerReturn.store_id == store_id, CustomerReturn.sale_id == sale_id)
            .order_by(CustomerReturn.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def get_by_idempotency_key(self, store_id: int, key: str) -> CustomerReturn | None:
        """同 (store_id, idempotency_key) 的退貨單；idempotent 重播用（防重複退現）。"""
        stmt = select(CustomerReturn).where(
            CustomerReturn.store_id == store_id,
            CustomerReturn.idempotency_key == key,
        )
        result: CustomerReturn | None = await self._session.scalar(stmt)
        return result

    async def returned_qty_by_sale_line_ids(
        self, store_id: int, sale_line_ids: list[int]
    ) -> dict[int, int]:
        if not sale_line_ids:
            return {}
        stmt = (
            select(ReturnLine.sale_line_id, func.coalesce(func.sum(ReturnLine.qty), 0))
            .where(ReturnLine.store_id == store_id, ReturnLine.sale_line_id.in_(sale_line_ids))
            .group_by(ReturnLine.sale_line_id)
        )
        rows = (await self._session.execute(stmt)).all()
        return {int(sale_line_id): int(qty) for sale_line_id, qty in rows}
