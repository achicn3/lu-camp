"""returns 資料存取層。"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import round_ntd
from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.returns.models import CustomerReturn, ReturnLine
from app.modules.sales.models import Sale, SaleLine
from app.shared.enums import OwnershipType, SaleInvoiceStatus, SaleLineType


@dataclass(frozen=True)
class ReturnsMarginAdjustments:
    """毛利報表的退貨扣減量（D-8(1)，裁示 2026-07-16：報表要扣退貨且按比例）。

    各欄為「應自 margin_components 對應桶**扣除**」的正值；退貨歸屬**退貨發生日**
    （落在查詢區間的 CustomerReturn），與退現出帳同日、跨期退貨不回改舊期報表。
    """

    owned_serialized_revenue: Decimal
    owned_serialized_cogs: Decimal
    owned_bulk_revenue: Decimal
    owned_bulk_cogs: Decimal
    consignment_serialized_revenue: Decimal
    consignment_bulk_revenue: Decimal
    catalog_revenue: Decimal
    no_cost_serialized_revenue: Decimal  # 缺成本自有序號（unknown 桶）


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

    async def margin_adjustments(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> ReturnsMarginAdjustments:
        """期間退貨的毛利扣減（read-only；退貨行 × 折後單價按比例，成本同倍率反轉）。

        note：returns 模組本就直接讀 sale_lines（見 service 的 SalesRepository 依賴），
        此處延續同一邊界慣例；serialized/bulk 成本口徑與 margin_components 正向計算一致
        （bulk 每件成本 round_ntd(批成本×件數÷批總件數)）。
        """
        o_ser_rev = o_ser_cogs = Decimal(0)
        o_bulk_rev = o_bulk_cogs = Decimal(0)
        c_ser_rev = c_bulk_rev = cat_rev = nocost_rev = Decimal(0)
        _zero = ReturnsMarginAdjustments(
            owned_serialized_revenue=Decimal(0),
            owned_serialized_cogs=Decimal(0),
            owned_bulk_revenue=Decimal(0),
            owned_bulk_cogs=Decimal(0),
            consignment_serialized_revenue=Decimal(0),
            consignment_bulk_revenue=Decimal(0),
            catalog_revenue=Decimal(0),
            no_cost_serialized_revenue=Decimal(0),
        )

        base = (
            select(SaleLine, ReturnLine.qty, CustomerReturn.created_at, ReturnLine.id)
            .join(ReturnLine, ReturnLine.sale_line_id == SaleLine.id)
            .join(CustomerReturn, CustomerReturn.id == ReturnLine.return_id)
            .join(Sale, Sale.id == SaleLine.sale_id)
            .where(
                CustomerReturn.store_id == store_id,
                CustomerReturn.created_at >= date_from,
                CustomerReturn.created_at < date_to,
                Sale.invoice_status != SaleInvoiceStatus.VOID,
            )
        )
        rows = [(r[0], r[1]) for r in (await self._session.execute(base)).all()]
        # 無退貨窗口（如 trends 逐桶大量呼叫）：即早返回零調整，避免 IN(0) 空查詢
        # 每桶多打兩趟 DB（365 天報表 = 730 次無謂往返；Codex 波次二第三輪 P2）。
        if not rows:
            return _zero
        # 散裝 COGS 反轉須用「差額法」逐行累積（Codex 第二輪 P2）：對每件成本捨入的散裝，
        # 三次退 1 件 ≠ 一次退 3 件。先算每個 sale_line 在**本期之前**的累積退貨量，
        # 反轉額＝round(cost×已退含本期/total) − round(cost×已退不含本期/total)。
        bulk_line_ids = [
            r[0].id
            for r in rows
            if r[0].line_type == SaleLineType.BULK_LOT and r[0].bulk_lot_id
        ]
        prior_returned: dict[int, int] = {}
        if bulk_line_ids:
            prior_rows = (
                await self._session.execute(
                    select(ReturnLine.sale_line_id, func.coalesce(func.sum(ReturnLine.qty), 0))
                    .join(CustomerReturn, CustomerReturn.id == ReturnLine.return_id)
                    .where(
                        CustomerReturn.store_id == store_id,
                        CustomerReturn.created_at < date_from,
                        ReturnLine.sale_line_id.in_(bulk_line_ids),
                    )
                    .group_by(ReturnLine.sale_line_id)
                )
            ).all()
            prior_returned = {int(lid): int(q) for lid, q in prior_rows}
        # 本期各散裝 sale_line 的退貨總量（同期多次退貨合併，才能對整段套差額）
        period_returned: dict[int, int] = {}
        for line, rqty in rows:
            if line.line_type == SaleLineType.BULK_LOT and line.bulk_lot_id:
                period_returned[line.id] = period_returned.get(line.id, 0) + rqty
        ser_ids = [
            r[0].serialized_item_id
            for r in rows
            if r[0].line_type == SaleLineType.SERIALIZED and r[0].serialized_item_id
        ]
        lot_ids = [
            r[0].bulk_lot_id
            for r in rows
            if r[0].line_type == SaleLineType.BULK_LOT and r[0].bulk_lot_id
        ]
        items = (
            {
                i.id: i
                for i in (
                    await self._session.scalars(
                        select(SerializedItem).where(SerializedItem.id.in_(ser_ids))
                    )
                ).all()
            }
            if ser_ids
            else {}
        )
        lots = (
            {
                b.id: b
                for b in (
                    await self._session.scalars(
                        select(BulkLot).where(BulkLot.id.in_(lot_ids))
                    )
                ).all()
            }
            if lot_ids
            else {}
        )
        cogs_done: set[int] = set()  # 散裝 COGS 每 sale_line 只以差額法算一次（非逐 row）
        for line, rqty in rows:
            refund = line.unit_price * rqty
            if line.line_type == SaleLineType.CATALOG:
                cat_rev += refund
            elif line.line_type == SaleLineType.BULK_LOT:
                lot = lots.get(line.bulk_lot_id or 0)
                if lot is not None and lot.consignor_id is not None:
                    c_bulk_rev += refund
                else:
                    o_bulk_rev += refund
                    if lot is not None and lot.total_qty and line.id not in cogs_done:
                        cogs_done.add(line.id)
                        prior = prior_returned.get(line.id, 0)
                        cum = prior + period_returned.get(line.id, 0)
                        cost, tq = lot.acquisition_cost, Decimal(lot.total_qty)
                        # 本期 COGS 反轉＝round(含本期累積) − round(本期前累積)
                        o_bulk_cogs += round_ntd(cost * Decimal(cum) / tq) - round_ntd(
                            cost * Decimal(prior) / tq
                        )
            elif line.line_type == SaleLineType.SERIALIZED:
                item = items.get(line.serialized_item_id or 0)
                if item is not None and item.ownership_type == OwnershipType.CONSIGNMENT:
                    c_ser_rev += refund
                elif item is not None and item.acquisition_cost is not None:
                    o_ser_rev += refund
                    o_ser_cogs += item.acquisition_cost
                else:
                    nocost_rev += refund
        return ReturnsMarginAdjustments(
            owned_serialized_revenue=o_ser_rev,
            owned_serialized_cogs=o_ser_cogs,
            owned_bulk_revenue=o_bulk_rev,
            owned_bulk_cogs=o_bulk_cogs,
            consignment_serialized_revenue=c_ser_rev,
            consignment_bulk_revenue=c_bulk_rev,
            catalog_revenue=cat_rev,
            no_cost_serialized_revenue=nocost_rev,
        )

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
