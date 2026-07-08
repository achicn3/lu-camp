"""acquisition 資料存取層（唯一直接碰本模組 ORM 的層）。"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.acquisition.models import Acquisition
from app.modules.inventory.models import BulkLot, SerializedItem
from app.shared.enums import AcquisitionType, PayoutMethod


class AcquisitionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, acquisition: Acquisition) -> Acquisition:
        self._session.add(acquisition)
        await self._session.flush()
        return acquisition

    async def get_by_idempotency_key(
        self, store_id: int, idempotency_key: str, *, for_update: bool = False
    ) -> Acquisition | None:
        stmt = select(Acquisition).where(
            Acquisition.store_id == store_id,
            Acquisition.idempotency_key == idempotency_key,
        )
        if for_update:
            # 回放決策前鎖列（Codex K4 第十五輪）：與 void_acquisition 的 FOR UPDATE 序列化，
            # 杜絕「回放讀到 void 前舊版（voided_at=NULL）而回 201」的競態。
            stmt = stmt.with_for_update()
        result: Acquisition | None = await self._session.scalar(stmt)
        return result

    async def get_by_signature_task_id(
        self, store_id: int, signature_task_id: int, *, for_update: bool = False
    ) -> Acquisition | None:
        """已綁定某手持切結的收購單（單次使用唯一約束 → 至多一張）。"""
        stmt = select(Acquisition).where(
            Acquisition.store_id == store_id,
            Acquisition.signature_task_id == signature_task_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: Acquisition | None = await self._session.scalar(stmt)
        return result

    async def get_codes(self, store_id: int, acquisition_id: int) -> tuple[list[str], str | None]:
        """重建該收購單的識別碼（冪等重放回應用）。"""
        items = await self._session.scalars(
            select(SerializedItem.item_code)
            .where(
                SerializedItem.store_id == store_id,
                SerializedItem.acquisition_id == acquisition_id,
            )
            .order_by(SerializedItem.id)
        )
        lot = await self._session.scalar(
            select(BulkLot.lot_code).where(
                BulkLot.store_id == store_id, BulkLot.acquisition_id == acquisition_id
            )
        )
        return list(items.all()), lot

    async def list_by_contact(
        self, store_id: int, contact_id: int, *, limit: int, offset: int
    ) -> list[Acquisition]:
        """某來源（賣方/寄售人）的收購單（會員帶來的商品來源；store 範圍、新到舊、分頁）。"""
        stmt = (
            select(Acquisition)
            .where(Acquisition.store_id == store_id, Acquisition.contact_id == contact_id)
            .order_by(Acquisition.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.scalars(stmt)
        return list(result)

    async def list_ids_by_contact(self, store_id: int, contact_id: int) -> list[int]:
        """某來源的所有收購單 id（id-only，供 sourced-items 反查買斷庫存；不載全列）。"""
        stmt = select(Acquisition.id).where(
            Acquisition.store_id == store_id, Acquisition.contact_id == contact_id
        )
        return list((await self._session.scalars(stmt)).all())

    async def get(self, store_id: int, acquisition_id: int) -> Acquisition | None:
        stmt = select(Acquisition).where(
            Acquisition.id == acquisition_id, Acquisition.store_id == store_id
        )
        result: Acquisition | None = await self._session.scalar(stmt)
        return result

    async def lock(self, store_id: int, acquisition_id: int) -> Acquisition | None:
        """收購列上 row lock 並刷新到已提交狀態（作廢前序列化、擋併發重複作廢；比照 lock_sale）。"""
        stmt = (
            select(Acquisition)
            .where(Acquisition.id == acquisition_id, Acquisition.store_id == store_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        result: Acquisition | None = await self._session.scalar(stmt)
        return result

    async def count_payouts_by_method(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> dict[PayoutMethod, int]:
        """期間內各撥款方式的收購筆數（SC-5b take_rate；唯讀）。

        只計「有撥款選擇」的收購（BUYOUT／BULK_LOT）；寄售（CONSIGNMENT）撥款屬結算階段、
        固定為 CASH 形狀但非現金 vs 購物金的選擇，計入會灌大分母、壓低 take_rate（Codex P2）。
        已作廢（voided_at 非空）者撥款已沖回、零效果，亦排除以免污染 take_rate（F6.5 Codex P2）。
        """
        stmt = (
            select(Acquisition.payout_method, func.count())
            .where(
                Acquisition.store_id == store_id,
                Acquisition.type != AcquisitionType.CONSIGNMENT,
                Acquisition.voided_at.is_(None),
                Acquisition.created_at >= date_from,
                Acquisition.created_at < date_to,
            )
            .group_by(Acquisition.payout_method)
        )
        rows = await self._session.execute(stmt)
        return {method: int(count) for method, count in rows}
