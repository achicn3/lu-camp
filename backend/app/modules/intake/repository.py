"""收購佇列資料存取層（唯一直接碰 ORM 的層）。"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.intake.models import (
    IntakeBatch,
    IntakeBatchAcquisition,
    IntakeDiscrepancy,
    IntakeLine,
)
from app.shared.enums import IntakeBatchStatus


class IntakeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def next_ticket_no(self, store_id: int, ticket_date: date) -> int:
        """同店同一營業日的下一個號碼（從 1 起）。撞號由唯一索引擋、服務層重取。"""
        current = await self._session.scalar(
            select(func.max(IntakeBatch.ticket_no)).where(
                IntakeBatch.store_id == store_id, IntakeBatch.ticket_date == ticket_date
            )
        )
        return (current or 0) + 1

    def add(
        self, row: IntakeBatch | IntakeLine | IntakeBatchAcquisition | IntakeDiscrepancy
    ) -> None:
        self._session.add(row)

    async def get_batch(
        self, store_id: int, batch_id: int, *, for_update: bool = False
    ) -> IntakeBatch | None:
        stmt = select(IntakeBatch).where(
            IntakeBatch.id == batch_id, IntakeBatch.store_id == store_id
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        result: IntakeBatch | None = await self._session.scalar(stmt)
        return result

    async def list_batches(
        self, store_id: int, statuses: list[IntakeBatchStatus] | None, *, limit: int, offset: int
    ) -> list[IntakeBatch]:
        """佇列清單：先到先處理（依建立時間），同時間依 id。"""
        stmt = select(IntakeBatch).where(IntakeBatch.store_id == store_id)
        if statuses is not None:
            stmt = stmt.where(IntakeBatch.status.in_(statuses))
        stmt = stmt.order_by(IntakeBatch.created_at, IntakeBatch.id).limit(limit).offset(offset)
        return list((await self._session.scalars(stmt)).all())

    async def lines_for(self, store_id: int, batch_ids: list[int]) -> list[IntakeLine]:
        if not batch_ids:
            return []
        stmt = (
            select(IntakeLine)
            .where(IntakeLine.store_id == store_id, IntakeLine.batch_id.in_(batch_ids))
            .order_by(IntakeLine.batch_id, IntakeLine.line_no)
        )
        return list((await self._session.scalars(stmt)).all())

    async def get_line(self, store_id: int, batch_id: int, line_id: int) -> IntakeLine | None:
        stmt = select(IntakeLine).where(
            IntakeLine.id == line_id,
            IntakeLine.batch_id == batch_id,
            IntakeLine.store_id == store_id,
        )
        result: IntakeLine | None = await self._session.scalar(stmt)
        return result

    async def next_line_no(self, batch_id: int) -> int:
        """刪過的列號不回收：列號是店員口頭對照用的，重複使用會對錯。"""
        current = await self._session.scalar(
            select(func.max(IntakeLine.line_no)).where(IntakeLine.batch_id == batch_id)
        )
        return (current or 0) + 1

    async def delete_line(self, line: IntakeLine) -> None:
        await self._session.delete(line)
        await self._session.flush()

    async def discrepancies_for(self, store_id: int, batch_id: int) -> list[IntakeDiscrepancy]:
        stmt = (
            select(IntakeDiscrepancy)
            .where(IntakeDiscrepancy.store_id == store_id, IntakeDiscrepancy.batch_id == batch_id)
            .order_by(IntakeDiscrepancy.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def bulk_shortages(self, store_id: int, lot_ids: list[int]) -> dict[int, int]:
        """散裝各堆記過的短少件數加總（算「上架了幾件」用：總件數扣掉短少）。"""
        if not lot_ids:
            return {}
        rows = await self._session.execute(
            select(IntakeDiscrepancy.bulk_lot_id, func.sum(IntakeDiscrepancy.qty))
            .where(
                IntakeDiscrepancy.store_id == store_id,
                IntakeDiscrepancy.bulk_lot_id.in_(lot_ids),
            )
            .group_by(IntakeDiscrepancy.bulk_lot_id)
        )
        return {int(lot_id): int(total) for lot_id, total in rows if lot_id is not None}

    async def acquisition_ids_for(
        self, store_id: int, batch_ids: list[int]
    ) -> dict[int, list[int]]:
        """每批付款時成立的收購 id（依成立順序）。"""
        if not batch_ids:
            return {}
        rows = await self._session.execute(
            select(IntakeBatchAcquisition.batch_id, IntakeBatchAcquisition.acquisition_id)
            .where(
                IntakeBatchAcquisition.store_id == store_id,
                IntakeBatchAcquisition.batch_id.in_(batch_ids),
            )
            .order_by(IntakeBatchAcquisition.id)
        )
        out: dict[int, list[int]] = {}
        for batch_id, acquisition_id in rows:
            out.setdefault(batch_id, []).append(acquisition_id)
        return out
