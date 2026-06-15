"""sales 資料存取層（唯一直接碰 ORM 的層）。

SC-5b §5B 毛利推導需要每行成本基礎：序號品/散裝批的取得成本存於 inventory 表，
本層以唯讀 join 取用（沿 acquisition repository 既有的 inventory 讀取模式；皆為唯讀報表查詢，
不寫他模組資料）。寄售抽成則由 sales service 經 consignment service 取得（§2）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.sales.models import Sale, SaleLine, SaleTender
from app.shared.enums import OwnershipType, SaleInvoiceStatus, SaleLineType, TenderType


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
    # ── SC-5b §5B 唯讀彙總 ──

    async def nonvoid_sale_ids(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> list[int]:
        """期間內未作廢的銷售 id（供寄售抽成依 sale_id 取數）。"""
        stmt = select(Sale.id).where(
            Sale.store_id == store_id,
            Sale.invoice_status != SaleInvoiceStatus.VOID,
            Sale.created_at >= date_from,
            Sale.created_at < date_to,
        )
        return list((await self._session.scalars(stmt)).all())

    async def goods_margin_and_revenue(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> tuple[Decimal, Decimal]:
        """二手商品的（買斷毛利, 商品收入）；未作廢、期間內。

        買斷毛利只認自有品（序號 OWNED：售價−取得成本；散裝自有：售價−每件成本×數量）。
        商品收入＝自有序號＋寄售序號＋自有散裝的售價（寄售收入計入分母，店家收入認抽成另計）。
        排除：數量型商品（catalog 無成本基礎）、寄售散裝（無抽成基礎）——皆於 docs/16 §5B 註明。
        """
        buyout_margin = Decimal(0)
        goods_revenue = Decimal(0)

        serialized = await self._session.execute(
            select(
                SerializedItem.ownership_type,
                SerializedItem.acquisition_cost,
                SaleLine.line_total,
            )
            .join(Sale, SaleLine.sale_id == Sale.id)
            .join(SerializedItem, SaleLine.serialized_item_id == SerializedItem.id)
            .where(
                Sale.store_id == store_id,
                Sale.invoice_status != SaleInvoiceStatus.VOID,
                Sale.created_at >= date_from,
                Sale.created_at < date_to,
                SaleLine.line_type == SaleLineType.SERIALIZED,
            )
        )
        for ownership, cost, line_total in serialized:
            goods_revenue += line_total
            if ownership == OwnershipType.OWNED and cost is not None:
                buyout_margin += line_total - cost

        bulk = await self._session.execute(
            select(
                BulkLot.acquisition_cost,
                BulkLot.total_qty,
                SaleLine.qty,
                SaleLine.line_total,
            )
            .join(Sale, SaleLine.sale_id == Sale.id)
            .join(BulkLot, SaleLine.bulk_lot_id == BulkLot.id)
            .where(
                Sale.store_id == store_id,
                Sale.invoice_status != SaleInvoiceStatus.VOID,
                Sale.created_at >= date_from,
                Sale.created_at < date_to,
                SaleLine.line_type == SaleLineType.BULK_LOT,
                BulkLot.consignor_id.is_(None),  # 自有散裝才認買斷毛利
            )
        )
        for acquisition_cost, total_qty, qty, line_total in bulk:
            goods_revenue += line_total
            if total_qty and total_qty > 0:
                cost = acquisition_cost * Decimal(qty) / Decimal(total_qty)
                buyout_margin += line_total - cost
        return buyout_margin, goods_revenue

    async def excess_spend_components(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> tuple[Decimal, Decimal]:
        """含購物金 tender 的未作廢銷售：(Σ total, Σ 現金部分)。

        現金部分 = Σ total − Σ 購物金 tender（這些銷售只會有 CASH/STORE_CREDIT 兩種 tender）。
        excess_spend_rate = 現金部分 ÷ total（docs/16 §5B）。
        """
        row = (
            await self._session.execute(
                select(
                    func.coalesce(func.sum(Sale.total), 0),
                    func.coalesce(func.sum(SaleTender.amount), 0),
                )
                .join(SaleTender, SaleTender.sale_id == Sale.id)
                .where(
                    Sale.store_id == store_id,
                    Sale.invoice_status != SaleInvoiceStatus.VOID,
                    Sale.created_at >= date_from,
                    Sale.created_at < date_to,
                    SaleTender.tender_type == TenderType.STORE_CREDIT,
                )
            )
        ).one()
        total_sum = Decimal(row[0])
        store_credit_sum = Decimal(row[1])
        return total_sum, total_sum - store_credit_sum

    async def member_purchase_count(
        self, store_id: int, contact_id: int, date_from: datetime, date_to: datetime
    ) -> int:
        """某會員在 [date_from, date_to) 的未作廢消費筆數（α 代理用）。"""
        stmt = select(func.count()).where(
            Sale.store_id == store_id,
            Sale.buyer_contact_id == contact_id,
            Sale.invoice_status != SaleInvoiceStatus.VOID,
            Sale.created_at >= date_from,
            Sale.created_at < date_to,
        )
        return int((await self._session.scalar(stmt)) or 0)
