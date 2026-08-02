"""sales 資料存取層（唯一直接碰 ORM 的層）。

SC-5b §5B 毛利推導需要每行成本基礎：序號品/散裝批的取得成本存於 inventory 表，
本層以唯讀 join 取用（沿 acquisition repository 既有的 inventory 讀取模式；皆為唯讀報表查詢，
不寫他模組資料）。寄售抽成則由 sales service 經 consignment service 取得（§2）。
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import round_ntd
from app.modules.inventory.models import BulkLot, SerializedItem
from app.modules.sales.models import (
    DiscountReason,
    GiftReason,
    LinePayRefundAttempt,
    LinePayTransaction,
    Sale,
    SaleAdjustment,
    SaleLine,
    SaleTender,
)
from app.shared.enums import (
    AdjustmentScope,
    LinePayRefundStatus,
    OwnershipType,
    SaleLineKind,
    SaleLineType,
    SaleStatus,
    TenderType,
)

# 經營洞察售出列：(brand_id, category_id, ownership, cost, commission_pct, intake, sold, line_total)
_SoldRowDB = tuple[
    int | None,
    int | None,
    OwnershipType,
    Decimal | None,
    int | None,
    datetime,
    datetime | None,
    Decimal,
]
# 散裝售出列：(brand_id, category_id, consignor_id, 整堆成本, 整堆件數, 本行件數,
#            intake, sold, line_total)
_BulkSoldRowDB = tuple[
    int | None,
    int | None,
    int | None,
    Decimal,
    int,
    int,
    datetime,
    datetime,
    Decimal,
]


@dataclass(frozen=True)
class SalesMarginComponents:
    """期間銷售毛利的原始組成（未作廢、唯讀；不含寄售抽成——由 service 經 consignment 取）。

    營收一律含稅、整數元。consignment 為寄售「全額售價」（gross turnover），店家收入只認抽成、
    另由 service 補上。unknown_revenue = 成本未建模/未知的營收（catalog + 缺成本的自有序號）。
    bulk 自有成本以「每件成本 = round_ntd(批成本 × 售出件數 ÷ 批總件數)」逐行四捨五入後加總
    （ROUND_HALF_UP，整數元；docs/19 §2.3 rounding policy）。
    """

    owned_serialized_revenue: Decimal
    owned_serialized_cogs: Decimal
    owned_bulk_revenue: Decimal
    owned_bulk_cogs: Decimal
    consignment_serialized_revenue: Decimal
    consignment_bulk_revenue: Decimal
    catalog_revenue: Decimal
    menu_revenue: Decimal  # 餐飲/內用（全額認列、成本未建模 → 計入 unknown_cost）
    unknown_cost_revenue: Decimal  # catalog + 餐飲 + 缺成本自有序號（營收認列但成本未知）
    cash_received: Decimal
    store_credit_redeemed: Decimal
    transaction_count: int
    # 支付手續費（docs/30 §7 決策 1）：各方式收款額＋手續費（店家成本），依 tender 分列。
    # payment_fee_total＝所有方式手續費合計；payment_methods＝(方法, 收款額, 手續費) 逐列。
    payment_fee_total: Decimal
    payment_methods: tuple[tuple[str, Decimal, Decimal], ...]
    # 臨時折扣總額（Σ 各行 manual_discount_amount，不含贈品行——贈品不參與折扣）。
    manual_discount_total: Decimal = Decimal(0)
    # 贈品：原價價值與成本各自獨立成桶。**贈品成本絕不混進商品毛利**——營收 0 加全額成本
    # 會讓毛利率失真；贈品的代價要單獨看見，不是把毛利拉成負的。
    gift_retail_value: Decimal = Decimal(0)
    gift_cost: Decimal = Decimal(0)


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

    async def list_active_gift_reasons(self, store_id: int) -> list[GiftReason]:
        stmt = (
            select(GiftReason)
            .where(GiftReason.store_id == store_id, GiftReason.is_active.is_(True))
            .order_by(GiftReason.sort_order, GiftReason.id)
        )
        return list(await self._session.scalars(stmt))

    async def list_all_gift_reasons(self, store_id: int) -> list[GiftReason]:
        """管理頁用：連停用的一起列（停用不實刪，歷史單據仍引用著）。"""
        stmt = (
            select(GiftReason)
            .where(GiftReason.store_id == store_id)
            .order_by(GiftReason.is_active.desc(), GiftReason.sort_order, GiftReason.id)
        )
        return list(await self._session.scalars(stmt))

    async def list_all_discount_reasons(self, store_id: int) -> list[DiscountReason]:
        stmt = (
            select(DiscountReason)
            .where(DiscountReason.store_id == store_id)
            .order_by(
                DiscountReason.is_active.desc(), DiscountReason.sort_order, DiscountReason.id
            )
        )
        return list(await self._session.scalars(stmt))

    async def get_gift_reason(self, store_id: int, reason_id: int) -> GiftReason | None:
        stmt = select(GiftReason).where(
            GiftReason.id == reason_id, GiftReason.store_id == store_id
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def get_discount_reason(self, store_id: int, reason_id: int) -> DiscountReason | None:
        stmt = select(DiscountReason).where(
            DiscountReason.id == reason_id, DiscountReason.store_id == store_id
        )
        return (await self._session.scalars(stmt)).one_or_none()

    async def add_reason(self, reason: GiftReason | DiscountReason) -> None:
        self._session.add(reason)
        await self._session.flush()

    async def list_active_discount_reasons(self, store_id: int) -> list[DiscountReason]:
        stmt = (
            select(DiscountReason)
            .where(DiscountReason.store_id == store_id, DiscountReason.is_active.is_(True))
            .order_by(DiscountReason.sort_order, DiscountReason.id)
        )
        return list(await self._session.scalars(stmt))

    async def list_tenders(self, sale_id: int) -> list[SaleTender]:
        stmt = select(SaleTender).where(SaleTender.sale_id == sale_id).order_by(SaleTender.id)
        result = await self._session.scalars(stmt)
        return list(result)

    async def add_linepay_transaction(self, txn: LinePayTransaction) -> LinePayTransaction:
        self._session.add(txn)
        await self._session.flush()
        return txn

    async def get_linepay_by_order_id(
        self, store_id: int, order_id: str, *, for_update: bool = False
    ) -> LinePayTransaction | None:
        """以 order_id 查 LINE Pay 交易（重試 check-first 用）。for_update 行鎖與並發重試序列化。"""
        stmt = select(LinePayTransaction).where(
            LinePayTransaction.store_id == store_id,
            LinePayTransaction.order_id == order_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: LinePayTransaction | None = await self._session.scalar(stmt)
        return result

    async def list_pending_refund_attempts(self, store_id: int) -> list[LinePayRefundAttempt]:
        """列出結果未定（PENDING）的 LINE Pay 退款嘗試（退款對帳頁：人工確認/解決）。"""
        stmt = (
            select(LinePayRefundAttempt)
            .where(
                LinePayRefundAttempt.store_id == store_id,
                LinePayRefundAttempt.status == LinePayRefundStatus.PENDING,
            )
            .order_by(LinePayRefundAttempt.id)
        )
        result = await self._session.scalars(stmt)
        return list(result)

    async def get_refund_attempt(
        self, store_id: int, attempt_id: int, *, for_update: bool = False
    ) -> LinePayRefundAttempt | None:
        stmt = select(LinePayRefundAttempt).where(
            LinePayRefundAttempt.store_id == store_id,
            LinePayRefundAttempt.id == attempt_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: LinePayRefundAttempt | None = await self._session.scalar(stmt)
        return result

    async def get_linepay_by_sale_id(
        self, store_id: int, sale_id: int, *, for_update: bool = False
    ) -> LinePayTransaction | None:
        """以 sale_id 查 LINE Pay 交易（退貨/作廢反轉時取交易反轉）。"""
        stmt = select(LinePayTransaction).where(
            LinePayTransaction.store_id == store_id,
            LinePayTransaction.sale_id == sale_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        result: LinePayTransaction | None = await self._session.scalar(stmt)
        return result

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

    async def get_by_idempotency_key(
        self, store_id: int, key: str, *, for_update: bool = False
    ) -> Sale | None:
        stmt = select(Sale).where(Sale.store_id == store_id, Sale.idempotency_key == key)
        if for_update:
            # 回放決策前鎖列（K4 第十五輪同款）：與 void_sale 的 FOR UPDATE 序列化，
            # 杜絕「回放讀到 void 前舊版而誤回成功」的競態。
            stmt = stmt.with_for_update()
        result: Sale | None = await self._session.scalar(stmt)
        return result

    async def get_by_signature_task_id(
        self, store_id: int, signature_task_id: int, *, for_update: bool = False
    ) -> Sale | None:
        """已綁定某購物金扣抵簽署的銷售（單次使用唯一約束 → 至多一筆；docs/23 K5）。"""
        stmt = select(Sale).where(
            Sale.store_id == store_id, Sale.signature_task_id == signature_task_id
        )
        if for_update:
            stmt = stmt.with_for_update()
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
            stmt = stmt.where(Sale.created_at < date_to)
        stmt = stmt.order_by(Sale.id.desc()).limit(limit).offset(offset)
        result = await self._session.scalars(stmt)
        return list(result)

    async def list_lines(self, sale_id: int) -> list[SaleLine]:
        stmt = select(SaleLine).where(SaleLine.sale_id == sale_id).order_by(SaleLine.id)
        result = await self._session.scalars(stmt)
        return list(result)

    async def get_serialized_sale_line(
        self, store_id: int, serialized_item_id: int
    ) -> tuple[SaleLine, Sale] | None:
        """某序號品最近一筆銷售明細＋其銷售單（庫存明細頁顯示實際成交價/售出時間用）。

        序號品至多賣出一次（不變量 1）；以 id 降冪取最近一筆，含退貨後的歷史亦可追。
        """
        stmt = (
            select(SaleLine, Sale)
            .join(Sale, Sale.id == SaleLine.sale_id)
            .where(
                SaleLine.store_id == store_id,
                SaleLine.serialized_item_id == serialized_item_id,
                # 排除已作廢銷售（Codex P2）：作廢已把品項回補為 IN_STOCK，
                # 不應再對在庫品顯示來自作廢交易的成交價/單號。
                Sale.status != SaleStatus.VOIDED,
            )
            .order_by(SaleLine.id.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).first()
        return (row[0], row[1]) if row is not None else None

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
        stmt = select(Sale).where(Sale.store_id == store_id, Sale.buyer_contact_id == contact_id)
        if date_from is not None:
            stmt = stmt.where(Sale.created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(Sale.created_at < date_to)
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

    async def serialized_sold_rows(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> list[_SoldRowDB]:
        """期間（未作廢）售出序號品的洞察原始列。

        欄位：品牌/類型/持有/成本/抽成%/入庫/售出/成交額；供經營洞察逐品牌/類型彙整。
        """
        rows = await self._session.execute(
            select(
                SerializedItem.brand_id,
                SerializedItem.category_id,
                SerializedItem.ownership_type,
                SerializedItem.acquisition_cost,
                SerializedItem.commission_pct,
                SerializedItem.intake_date,
                # 售出時間取「該銷售」的時間（Sale.created_at），而非 item.sold_date——
                # 後者是可變狀態（退貨清空、再售覆寫），會讓歷史期間的在庫天數算錯（Codex P2）。
                Sale.created_at,
                SaleLine.line_total,
            )
            .join(Sale, SaleLine.sale_id == Sale.id)
            .join(SerializedItem, SaleLine.serialized_item_id == SerializedItem.id)
            .where(
                Sale.store_id == store_id,
                Sale.status != SaleStatus.VOIDED,
                Sale.created_at >= date_from,
                Sale.created_at < date_to,
                SaleLine.line_type == SaleLineType.SERIALIZED,
            )
        )
        return [tuple(r) for r in rows]

    async def bulk_sold_rows(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> list[_BulkSoldRowDB]:
        """期間（未作廢）售出散裝的洞察原始列。

        欄位：品牌/類型/寄售人/整堆成本/整堆件數/本行件數/入庫/該銷售時間/成交額；
        供經營洞察把散裝也納入品牌/類型排行（Codex P2）。每件成本＝整堆成本÷整堆件數。
        """
        rows = await self._session.execute(
            select(
                BulkLot.brand_id,
                BulkLot.category_id,
                BulkLot.consignor_id,
                BulkLot.acquisition_cost,
                BulkLot.total_qty,
                SaleLine.qty,
                BulkLot.intake_date,
                Sale.created_at,
                SaleLine.line_total,
            )
            .join(Sale, SaleLine.sale_id == Sale.id)
            .join(BulkLot, SaleLine.bulk_lot_id == BulkLot.id)
            .where(
                Sale.store_id == store_id,
                Sale.status != SaleStatus.VOIDED,
                Sale.created_at >= date_from,
                Sale.created_at < date_to,
                SaleLine.line_type == SaleLineType.BULK_LOT,
            )
        )
        return [tuple(r) for r in rows]

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
            Sale.status != SaleStatus.VOIDED,
            Sale.created_at >= date_from,
            Sale.created_at < date_to,
        )
        return list((await self._session.scalars(stmt)).all())

    async def discount_totals_by_campaign(self, store_id: int) -> dict[int, Decimal]:
        """各活動實際造成的折讓總額（非作廢）：Σ sale_line.discount_amount group by campaign_id。

        供活動成效報表（C4）精確歸屬「此活動發出的折讓」（以 sale_line.campaign_id 為準，
        非以期間概算）。作廢單以 invoice_status != VOID 排除（與毛利口徑一致）。
        """
        stmt = (
            select(
                SaleLine.campaign_id,
                func.coalesce(func.sum(SaleLine.discount_amount), 0),
            )
            .join(Sale, SaleLine.sale_id == Sale.id)
            .where(
                Sale.store_id == store_id,
                Sale.status != SaleStatus.VOIDED,
                SaleLine.campaign_id.is_not(None),
            )
            .group_by(SaleLine.campaign_id)
        )
        rows = await self._session.execute(stmt)
        return {cid: Decimal(total) for cid, total in rows if cid is not None}

    async def goods_margin_and_revenue(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> tuple[Decimal, Decimal]:
        """二手商品的（買斷毛利, 商品收入）；未作廢、期間內。

        買斷毛利只認自有品（序號 OWNED：售價−取得成本；散裝自有：售價−每件成本×數量）。
        商品收入＝自有序號＋寄售序號＋自有散裝的售價（寄售收入計入分母，店家收入認抽成另計）。
        排除：一般商品（catalog 無成本基礎）、寄售散裝（無抽成基礎）——皆於 docs/16 §5B 註明。
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
                Sale.status != SaleStatus.VOIDED,
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
                Sale.status != SaleStatus.VOIDED,
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

    async def discount_rows(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> tuple[list[tuple[int | None, str, int, Decimal, Decimal]], list[tuple[int, int, Decimal]]]:
        """期間內的臨時折扣：依原因、依店員各彙總一份。

        金額取落盤的 `applied_amount`，**不重算**——商品價格與活動日後都會變。
        作廢的折扣（`voided_at` 有值）不計入。
        """
        by_reason = list(
            await self._session.execute(
                select(
                    SaleAdjustment.reason_id,
                    func.coalesce(SaleAdjustment.reason_name, "未指定原因"),
                    func.count(SaleAdjustment.id),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    SaleAdjustment.scope == AdjustmentScope.ITEM,
                                    SaleAdjustment.applied_amount,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    SaleAdjustment.scope == AdjustmentScope.ORDER,
                                    SaleAdjustment.applied_amount,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                )
                .join(Sale, SaleAdjustment.sale_id == Sale.id)
                .where(
                    SaleAdjustment.store_id == store_id,
                    SaleAdjustment.voided_at.is_(None),
                    Sale.status != SaleStatus.VOIDED,
                    Sale.created_at >= date_from,
                    Sale.created_at < date_to,
                )
                .group_by(SaleAdjustment.reason_id, SaleAdjustment.reason_name)
                .order_by(func.sum(SaleAdjustment.applied_amount).desc())
            )
        )
        by_clerk = list(
            await self._session.execute(
                select(
                    SaleAdjustment.created_by,
                    func.count(SaleAdjustment.id),
                    func.coalesce(func.sum(SaleAdjustment.applied_amount), 0),
                )
                .join(Sale, SaleAdjustment.sale_id == Sale.id)
                .where(
                    SaleAdjustment.store_id == store_id,
                    SaleAdjustment.voided_at.is_(None),
                    Sale.status != SaleStatus.VOIDED,
                    Sale.created_at >= date_from,
                    Sale.created_at < date_to,
                )
                .group_by(SaleAdjustment.created_by)
                .order_by(func.sum(SaleAdjustment.applied_amount).desc())
            )
        )
        return (
            [(r[0], str(r[1]), int(r[2]), Decimal(r[3]), Decimal(r[4])) for r in by_reason],
            [(int(r[0]), int(r[1]), Decimal(r[2])) for r in by_clerk],
        )

    async def gift_rows(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> tuple[
        list[tuple[int | None, str, int, int, Decimal, Decimal]],
        list[tuple[str, int, Decimal, Decimal]],
    ]:
        """期間內送出的贈品：依原因、依品項各彙總一份。

        原價價值＝`original_unit_price × qty`（贈品的 CHECK 保證它不為 NULL）；
        成本取成交當下的快照，未知則為 0（不假造）。
        """
        base = (
            select(SaleLine)
            .join(Sale, SaleLine.sale_id == Sale.id)
            .where(
                Sale.store_id == store_id,
                Sale.status != SaleStatus.VOIDED,
                Sale.created_at >= date_from,
                Sale.created_at < date_to,
                SaleLine.line_kind == SaleLineKind.GIFT,
            )
        )
        retail = func.coalesce(func.sum(SaleLine.original_unit_price * SaleLine.qty), 0)
        cost = func.coalesce(func.sum(SaleLine.cost_snapshot), 0)
        by_reason = list(
            await self._session.execute(
                base.with_only_columns(
                    SaleLine.gift_reason_id,
                    func.coalesce(SaleLine.gift_reason_name, "未指定原因"),
                    func.count(SaleLine.id),
                    func.coalesce(func.sum(SaleLine.qty), 0),
                    retail,
                    cost,
                )
                .group_by(SaleLine.gift_reason_id, SaleLine.gift_reason_name)
                .order_by(retail.desc())
            )
        )
        by_product = list(
            await self._session.execute(
                base.with_only_columns(
                    SaleLine.description,
                    func.coalesce(func.sum(SaleLine.qty), 0),
                    retail,
                    cost,
                )
                .group_by(SaleLine.description)
                .order_by(retail.desc())
            )
        )
        return (
            [
                (r[0], str(r[1]), int(r[2]), int(r[3]), Decimal(r[4]), Decimal(r[5]))
                for r in by_reason
            ],
            [(str(r[0]), int(r[1]), Decimal(r[2]), Decimal(r[3])) for r in by_product],
        )

    async def margin_components(
        self, store_id: int, date_from: datetime, date_to: datetime
    ) -> SalesMarginComponents:
        """期間（未作廢）銷售毛利原始組成（read-only join；不含寄售抽成，見 service）。"""
        owned_serialized_revenue = Decimal(0)
        owned_serialized_cogs = Decimal(0)
        consignment_serialized_revenue = Decimal(0)
        unknown_cost_revenue = Decimal(0)

        serialized = await self._session.execute(
            select(
                SerializedItem.ownership_type,
                # 成本一律優先取**成交當下的快照**：日後調整商品成本不得回頭改寫歷史毛利。
                # 舊資料無快照才回退即時 join（回填後兩者相同，見 b5d7f9a1c3e2）。
                func.coalesce(SaleLine.cost_snapshot, SerializedItem.acquisition_cost),
                SaleLine.net_amount,
            )
            .join(Sale, SaleLine.sale_id == Sale.id)
            .join(SerializedItem, SaleLine.serialized_item_id == SerializedItem.id)
            .where(
                Sale.store_id == store_id,
                Sale.status != SaleStatus.VOIDED,
                Sale.created_at >= date_from,
                Sale.created_at < date_to,
                SaleLine.line_type == SaleLineType.SERIALIZED,
                SaleLine.line_kind != SaleLineKind.GIFT,
            )
        )
        for ownership, cost, net_amount in serialized:
            if ownership == OwnershipType.CONSIGNMENT:
                consignment_serialized_revenue += net_amount
            elif cost is not None:  # 自有且有成本 → 認列營收與成本
                owned_serialized_revenue += net_amount
                owned_serialized_cogs += cost
            else:  # 自有但缺成本：營收認列、成本未知（不假造毛利）
                unknown_cost_revenue += net_amount

        owned_bulk_revenue = Decimal(0)
        owned_bulk_cogs = Decimal(0)
        consignment_bulk_revenue = Decimal(0)
        bulk = await self._session.execute(
            select(
                BulkLot.consignor_id,
                BulkLot.acquisition_cost,
                BulkLot.total_qty,
                SaleLine.qty,
                SaleLine.net_amount,
                SaleLine.cost_snapshot,
            )
            .join(Sale, SaleLine.sale_id == Sale.id)
            .join(BulkLot, SaleLine.bulk_lot_id == BulkLot.id)
            .where(
                Sale.store_id == store_id,
                Sale.status != SaleStatus.VOIDED,
                Sale.created_at >= date_from,
                Sale.created_at < date_to,
                SaleLine.line_type == SaleLineType.BULK_LOT,
                SaleLine.line_kind != SaleLineKind.GIFT,
            )
        )
        for consignor_id, acquisition_cost, total_qty, qty, net_amount, cost_snapshot in bulk:
            if consignor_id is not None:  # 寄售散裝：全額計流水，無抽成基礎、不認自有成本
                consignment_bulk_revenue += net_amount
                continue
            owned_bulk_revenue += net_amount
            if cost_snapshot is not None:
                owned_bulk_cogs += cost_snapshot
            elif total_qty and total_qty > 0:
                owned_bulk_cogs += round_ntd(acquisition_cost * Decimal(qty) / Decimal(total_qty))

        catalog_revenue = Decimal(
            (
                await self._session.execute(
                    select(func.coalesce(func.sum(SaleLine.net_amount), 0))
                    .join(Sale, SaleLine.sale_id == Sale.id)
                    .where(
                        Sale.store_id == store_id,
                        Sale.status != SaleStatus.VOIDED,
                        Sale.created_at >= date_from,
                        Sale.created_at < date_to,
                        SaleLine.line_type == SaleLineType.CATALOG,
                        SaleLine.line_kind != SaleLineKind.GIFT,
                    )
                )
            ).scalar_one()
        )

        # 餐飲/內用營收：全額認列但成本未建模（同 catalog，計入 unknown_cost、不灌毛利率）。
        menu_revenue = Decimal(
            (
                await self._session.execute(
                    select(func.coalesce(func.sum(SaleLine.net_amount), 0))
                    .join(Sale, SaleLine.sale_id == Sale.id)
                    .where(
                        Sale.store_id == store_id,
                        Sale.status != SaleStatus.VOIDED,
                        Sale.created_at >= date_from,
                        Sale.created_at < date_to,
                        SaleLine.line_type == SaleLineType.MENU,
                        SaleLine.line_kind != SaleLineKind.GIFT,
                    )
                )
            ).scalar_one()
        )

        tender_rows = list(
            await self._session.execute(
                select(
                    SaleTender.tender_type,
                    func.coalesce(func.sum(SaleTender.amount), 0),
                    func.coalesce(func.sum(SaleTender.fee_amount), 0),
                )
                .join(Sale, SaleTender.sale_id == Sale.id)
                .where(
                    Sale.store_id == store_id,
                    Sale.status != SaleStatus.VOIDED,
                    Sale.created_at >= date_from,
                    Sale.created_at < date_to,
                )
                .group_by(SaleTender.tender_type)
            )
        )
        cash_received = Decimal(0)
        store_credit_redeemed = Decimal(0)
        payment_fee_total = Decimal(0)
        # 依 tender 型別列舉順序穩定輸出各方式（收款額, 手續費），供報表分列。
        by_type: dict[TenderType, tuple[Decimal, Decimal]] = {}
        for tender_type, amount, fee in tender_rows:
            by_type[tender_type] = (Decimal(amount), Decimal(fee))
            payment_fee_total += Decimal(fee)
            if tender_type == TenderType.CASH:
                cash_received = Decimal(amount)
            elif tender_type == TenderType.STORE_CREDIT:
                store_credit_redeemed = Decimal(amount)
        payment_methods = tuple(
            (t.value, by_type[t][0], by_type[t][1]) for t in TenderType if t in by_type
        )

        transaction_count = int(
            (
                await self._session.execute(
                    select(func.count(Sale.id)).where(
                        Sale.store_id == store_id,
                        Sale.status != SaleStatus.VOIDED,
                        Sale.created_at >= date_from,
                        Sale.created_at < date_to,
                    )
                )
            ).scalar_one()
        )

        # 臨時折扣與贈品各自成桶：折扣是「少收的錢」，贈品是「送出去的成本」，兩者性質不同，
        # 混在一起就再也拆不開（活動報表直接 SUM 折扣欄位）。
        manual_discount_total = Decimal(
            (
                await self._session.execute(
                    select(func.coalesce(func.sum(SaleLine.manual_discount_amount), 0))
                    .join(Sale, SaleLine.sale_id == Sale.id)
                    .where(
                        Sale.store_id == store_id,
                        Sale.status != SaleStatus.VOIDED,
                        Sale.created_at >= date_from,
                        Sale.created_at < date_to,
                    )
                )
            ).scalar_one()
        )
        gift_row = (
            await self._session.execute(
                select(
                    func.coalesce(
                        func.sum(SaleLine.original_unit_price * SaleLine.qty), 0
                    ),
                    func.coalesce(func.sum(SaleLine.cost_snapshot), 0),
                )
                .join(Sale, SaleLine.sale_id == Sale.id)
                .where(
                    Sale.store_id == store_id,
                    Sale.status != SaleStatus.VOIDED,
                    Sale.created_at >= date_from,
                    Sale.created_at < date_to,
                    SaleLine.line_kind == SaleLineKind.GIFT,
                )
            )
        ).one()

        unknown_cost_revenue += catalog_revenue + menu_revenue
        return SalesMarginComponents(
            manual_discount_total=manual_discount_total,
            gift_retail_value=Decimal(gift_row[0]),
            gift_cost=Decimal(gift_row[1]),
            owned_serialized_revenue=owned_serialized_revenue,
            owned_serialized_cogs=owned_serialized_cogs,
            owned_bulk_revenue=owned_bulk_revenue,
            owned_bulk_cogs=owned_bulk_cogs,
            consignment_serialized_revenue=consignment_serialized_revenue,
            consignment_bulk_revenue=consignment_bulk_revenue,
            catalog_revenue=catalog_revenue,
            menu_revenue=menu_revenue,
            unknown_cost_revenue=unknown_cost_revenue,
            cash_received=cash_received,
            store_credit_redeemed=store_credit_redeemed,
            transaction_count=transaction_count,
            payment_fee_total=payment_fee_total,
            payment_methods=payment_methods,
        )

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
                    Sale.status != SaleStatus.VOIDED,
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
            Sale.status != SaleStatus.VOIDED,
            Sale.created_at >= date_from,
            Sale.created_at < date_to,
        )
        return int((await self._session.scalar(stmt)) or 0)
