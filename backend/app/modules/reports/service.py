"""SC-4 購物金報表 service：彙整 storecredit / contacts service 的唯讀資料成報表。

只透過對方 service 取數（不直接碰他模組資料表，CLAUDE.md §2）；數值全部從帳本推導
（docs/16 §5），本層不寫任何資料。
"""

import calendar
from collections import OrderedDict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import round_ntd, split_tax_inclusive
from app.modules.campaigns.service import CampaignService
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.consignment.service import ConsignmentService
from app.modules.contacts.service import ContactService
from app.modules.inventory.service import InventoryService
from app.modules.reports.aging import BUCKET_KEYS as INVENTORY_BUCKET_KEYS
from app.modules.reports.aging import _bucket_for_age
from app.modules.reports.schemas import (
    ALPHA_METHOD_NOTE,
    ESTIMATE_FIELDS,
    AgingBuckets,
    CampaignPerformanceReport,
    CampaignPerformanceRow,
    ConsignmentPayableRow,
    ConsignmentPayablesReport,
    DailyCashReport,
    DailyCashSessionRow,
    DailySummaryReport,
    EffectivenessReport,
    FlowRow,
    FlowsReport,
    InventoryValueReport,
    LiabilityReport,
    MemberBalanceRow,
    ReconciliationReport,
    SalesMarginReport,
    TrendRow,
    TrendsReport,
)
from app.modules.sales.service import SalesService
from app.modules.settings.service import StoreSettingsService
from app.modules.storecredit.service import StoreCreditService
from app.modules.storecredit.suggestion_service import PremiumSuggestionService
from app.shared.enums import CampaignStatus, OwnershipType
from app.shared.exceptions import DomainError


def _now() -> datetime:
    return datetime.now(UTC)


MAX_TREND_BUCKETS = 400  # 防呆：日粒度跨年等過多桶 → 422（單店報表合理上限）


def _bucket_bounds(granularity: str, dt: datetime) -> tuple[datetime, datetime]:
    """回傳 dt 所屬桶的 [起, 下一桶起)（UTC、aligned）。granularity=day/week/month/quarter。"""
    day = datetime(dt.year, dt.month, dt.day, tzinfo=UTC)
    if granularity == "day":
        return day, day + timedelta(days=1)
    if granularity == "week":  # ISO 週，週一為起
        start = day - timedelta(days=day.weekday())
        return start, start + timedelta(days=7)
    if granularity == "month":
        start = datetime(dt.year, dt.month, 1, tzinfo=UTC)
        nxt = (
            datetime(dt.year + 1, 1, 1, tzinfo=UTC)
            if dt.month == 12
            else datetime(dt.year, dt.month + 1, 1, tzinfo=UTC)
        )
        return start, nxt
    # quarter：季首月 = 1/4/7/10
    q_month = ((dt.month - 1) // 3) * 3 + 1
    start = datetime(dt.year, q_month, 1, tzinfo=UTC)
    nxt = (
        datetime(dt.year + 1, 1, 1, tzinfo=UTC)
        if q_month == 10
        else datetime(dt.year, q_month + 3, 1, tzinfo=UTC)
    )
    return start, nxt


def _age_bucket(now: datetime, intake: datetime) -> str:
    """入庫至今的帳齡桶 key（<30/30-90/90-180/180-365/>365 天；沿用 aging.py 邊界）。"""
    age_days = (now - intake).total_seconds() / 86400
    return _bucket_for_age(age_days)


def _health_ratio(total_outstanding: Decimal, monthly_outflow: Decimal) -> str | None:
    """負債健康比 = 未兌付總負債 ÷ 月固定現金支出（docs/16 §5A）；分母 0 → N/A（null）。"""
    if monthly_outflow <= 0:
        return None
    return str((total_outstanding / monthly_outflow).quantize(Decimal("0.01")))


class ReportsService:
    def __init__(self, session: AsyncSession) -> None:
        self._sc = StoreCreditService(session)
        self._contacts = ContactService(session)
        self._settings = StoreSettingsService(session)
        self._suggestion = PremiumSuggestionService(session)
        self._cash = CashDrawerService(session)
        self._sales = SalesService(session)
        self._inventory = InventoryService(session)
        self._consignment = ConsignmentService(session)
        self._campaigns = CampaignService(session)

    async def consignment_payables(
        self, store_id: int, *, status_filter: str
    ) -> ConsignmentPayablesReport:
        """寄售應付（docs/19 §2.5）：只計 PENDING 待付；PAID/CANCELLED/reclaim 分欄、不沖抵。

        合計恆涵蓋全部狀態；status_filter（PENDING/PAID/CANCELLED/ALL）只決定明細列。唯讀；
        只輸出寄售人姓名/電話，不含 national_id。
        """
        all_rows = await self._consignment.all_settlements_for_report(store_id)
        total_pending = Decimal(0)
        total_paid = Decimal(0)
        total_cancelled = Decimal(0)
        total_reclaim = Decimal(0)
        rows: list[ConsignmentPayableRow] = []
        for r in all_rows:
            status = r["status"].value if hasattr(r["status"], "value") else str(r["status"])
            payout = Decimal(r["payout_amount"])
            if status == "PENDING":
                total_pending += payout
            elif status == "PAID":
                total_paid += payout
            elif status == "CANCELLED":
                total_cancelled += payout
            if r["reclaim_needed"]:
                total_reclaim += payout
            if status_filter != "ALL" and status != status_filter:
                continue
            rows.append(
                ConsignmentPayableRow(
                    settlement_id=r["id"],
                    consignor_id=r["consignor_id"],
                    consignor_name=r["consignor_name"],
                    consignor_phone=r["consignor_phone"],
                    sale_id=r["sale_id"],
                    item_code=r["item_code"],
                    item_name=r["item_name"],
                    gross=Decimal(r["gross"]),
                    commission_amount=Decimal(r["commission_amount"]),
                    payout_amount=payout,
                    status=status,
                    reclaim_needed=bool(r["reclaim_needed"]),
                    sale_created_at=r["sale_created_at"],
                )
            )
        return ConsignmentPayablesReport(
            generated_at=_now(),
            store_id=store_id,
            status_filter=status_filter,
            rows=rows,
            total_pending_payout=total_pending,
            total_paid_payout=total_paid,
            total_cancelled_payout=total_cancelled,
            total_reclaim_needed_payout=total_reclaim,
        )

    async def inventory_value(self, store_id: int) -> InventoryValueReport:
        """庫存價值與庫齡（docs/19 §2.4）：自有計成本、寄售另列售價、catalog 成本 N/A。

        aging = 自有在庫成本價值按入庫時間（intake_date）分桶（Σ = total_owned_cost_value）。
        已售/退場（IN_STOCK 以外、bulk remaining=0）不入；唯讀。
        """
        now = _now()
        owned_ser_count = 0
        owned_ser_cost = Decimal(0)
        owned_ser_retail = Decimal(0)
        consign_ser_count = 0
        consign_gross = Decimal(0)
        aging = OrderedDict((k, Decimal(0)) for k in INVENTORY_BUCKET_KEYS)

        for item in await self._inventory.serialized_for_valuation(store_id):
            if item.ownership_type == OwnershipType.CONSIGNMENT:
                consign_ser_count += 1
                consign_gross += item.listed_price
                continue
            cost = item.acquisition_cost or Decimal(0)
            owned_ser_count += 1
            owned_ser_cost += cost
            owned_ser_retail += item.listed_price
            aging[_age_bucket(now, item.intake_date)] += cost

        owned_bulk_qty = 0
        owned_bulk_cost = Decimal(0)
        owned_bulk_retail = Decimal(0)
        consign_bulk_qty = 0
        for lot in await self._inventory.bulk_for_valuation(store_id):
            remaining = lot.remaining_qty
            retail = lot.unit_price * Decimal(remaining)
            if lot.consignor_id is not None:
                consign_bulk_qty += remaining
                consign_gross += retail
                continue
            cost = (
                Decimal(
                    round_ntd(lot.acquisition_cost * Decimal(remaining) / Decimal(lot.total_qty))
                )
                if lot.total_qty > 0
                else Decimal(0)
            )
            owned_bulk_qty += remaining
            owned_bulk_cost += cost
            owned_bulk_retail += retail
            aging[_age_bucket(now, lot.intake_date)] += cost

        catalog_qty = 0
        catalog_retail = Decimal(0)
        for product in await self._inventory.catalog_for_valuation(store_id):
            catalog_qty += product.quantity_on_hand
            catalog_retail += product.unit_price * Decimal(product.quantity_on_hand)

        total_owned_cost = owned_ser_cost + owned_bulk_cost
        total_owned_retail = owned_ser_retail + owned_bulk_retail
        return InventoryValueReport(
            generated_at=now,
            store_id=store_id,
            owned_serialized_count=owned_ser_count,
            owned_serialized_cost=owned_ser_cost,
            owned_serialized_retail=owned_ser_retail,
            owned_bulk_remaining_qty=owned_bulk_qty,
            owned_bulk_cost=owned_bulk_cost,
            owned_bulk_retail=owned_bulk_retail,
            total_owned_cost_value=total_owned_cost,
            total_owned_retail_value=total_owned_retail,
            consignment_serialized_count=consign_ser_count,
            consignment_bulk_remaining_qty=consign_bulk_qty,
            consignment_inventory_gross=consign_gross,
            catalog_total_qty=catalog_qty,
            catalog_retail_value=catalog_retail,
            catalog_cost_value=None,
            owned_cost_aging=AgingBuckets(
                lt_30d=aging["lt_30d"],
                d30_90=aging["d30_90"],
                d90_180=aging["d90_180"],
                d180_365=aging["d180_365"],
                gt_365d=aging["gt_365d"],
            ),
        )

    async def sales_margin(
        self, store_id: int, *, date_from: datetime, date_to: datetime
    ) -> SalesMarginReport:
        """銷售 / 毛利報表（docs/19 §2.3）：未作廢；買斷認成本、寄售只認抽成、catalog 成本 N/A。"""
        bd = await self._sales.margin_breakdown(store_id, date_from, date_to)
        return SalesMarginReport(
            generated_at=_now(),
            store_id=store_id,
            date_from=date_from,
            date_to=date_to,
            gross_turnover=bd.gross_turnover,
            recognized_revenue=bd.recognized_revenue,
            owned_cogs=bd.owned_cogs,
            bulk_cogs=bd.bulk_cogs,
            consignment_commission_income=bd.consignment_commission_income,
            gross_margin=bd.gross_margin,
            gross_margin_rate=bd.gross_margin_rate,
            unknown_cost_sales=bd.unknown_cost_sales,
            food_revenue=bd.food_revenue,
            secondhand_revenue=bd.secondhand_revenue,
            cash_received=bd.cash_received,
            store_credit_redeemed=bd.store_credit_redeemed,
            transaction_count=bd.transaction_count,
        )

    async def campaign_performance(self, store_id: int) -> CampaignPerformanceReport:
        """活動成效報表（docs/21 C4）：每檔生效中/已結束活動的營運成效 + 其發出的折讓。唯讀。

        營運指標以活動排定區間 [starts_at, ends_at) 取 margin_breakdown（與 R2 同源、半開區間）；
        折讓總額依 sale_line.campaign_id 精確歸屬（非區間概算）。DRAFT/CANCELLED 無成交、不列。
        依 starts_at 新到舊排序。
        """
        campaigns = await self._campaigns.list_campaigns(store_id)
        discount_totals = await self._sales.discount_totals_by_campaign(store_id)
        rows: list[CampaignPerformanceRow] = []
        for c in campaigns:
            if c.status not in (CampaignStatus.ACTIVE, CampaignStatus.ENDED):
                continue
            # 區間 [starts_at, ends_at)；模型 CHECK 保證 ends_at > starts_at（滿足 from<to）。
            bd = await self._sales.margin_breakdown(store_id, c.starts_at, c.ends_at)
            rows.append(
                CampaignPerformanceRow(
                    campaign_id=c.id,
                    name=c.name,
                    status=c.status,
                    discount_pct=c.discount_pct,
                    starts_at=c.starts_at,
                    ends_at=c.ends_at,
                    campaign_discount_total=discount_totals.get(c.id, Decimal(0)),
                    gross_turnover=bd.gross_turnover,
                    recognized_revenue=bd.recognized_revenue,
                    gross_margin=bd.gross_margin,
                    gross_margin_rate=bd.gross_margin_rate,
                    transaction_count=bd.transaction_count,
                )
            )
        rows.sort(key=lambda r: r.starts_at, reverse=True)
        return CampaignPerformanceReport(generated_at=_now(), store_id=store_id, rows=rows)

    async def daily_cash(self, store_id: int, report_date: date) -> DailyCashReport:
        """每日現金對帳（docs/19 §2.2）：依 opened_at 的 UTC 日 [date, date+1) 取本店 session。

        每 session 的 expected 與關帳同源（cashdrawer `session_breakdown`）。購物金兌付總額另計、
        只展示不進現金 expected（CLAUDE.md §6）。無 session 日回空 sessions + 全 0 合計（非 500）。
        """
        now = _now()
        start = datetime(report_date.year, report_date.month, report_date.day, tzinfo=UTC)
        end = start + timedelta(days=1)
        sessions = await self._cash.list_sessions_in_range(store_id, start, end)

        rows: list[DailyCashSessionRow] = []
        totals = dict.fromkeys(
            (
                "opening_float",
                "cash_sales",
                "acquisition_void_in",
                "buyout_out",
                "consignment_payout_out",
                "sale_refund_out",
                "manual_adjust",
                "expected",
                "counted",
                "variance",
            ),
            Decimal(0),
        )
        for session in sessions:
            bd = await self._cash.session_breakdown(session)
            rows.append(
                DailyCashSessionRow(
                    session_id=session.id,
                    status=session.status.value,
                    opened_at=session.opened_at,
                    closed_at=session.closed_at,
                    opened_by=session.opened_by,
                    closed_by=session.closed_by,
                    opening_float=session.opening_float,
                    cash_sales=bd.cash_sales,
                    acquisition_void_in=bd.acquisition_void_in,
                    buyout_out=bd.buyout_out,
                    consignment_payout_out=bd.consignment_payout_out,
                    sale_refund_out=bd.sale_refund_out,
                    manual_adjust_total=bd.manual_adjust_total,
                    expected_amount=bd.expected,
                    counted_amount=session.counted_amount,
                    variance=session.variance,
                )
            )
            totals["opening_float"] += session.opening_float
            totals["cash_sales"] += bd.cash_sales
            totals["acquisition_void_in"] += bd.acquisition_void_in
            totals["buyout_out"] += bd.buyout_out
            totals["consignment_payout_out"] += bd.consignment_payout_out
            totals["sale_refund_out"] += bd.sale_refund_out
            totals["manual_adjust"] += bd.manual_adjust_total
            totals["expected"] += bd.expected
            if session.counted_amount is not None:
                totals["counted"] += session.counted_amount
            if session.variance is not None:
                totals["variance"] += session.variance

        sc_rows = await self._sc.flows(store_id, date_from=start, date_to=end, granularity="day")
        sc_redeemed = Decimal(0)
        for row in sc_rows:
            redeemed = row["redeemed"]
            assert isinstance(redeemed, Decimal)
            sc_redeemed += redeemed

        return DailyCashReport(
            generated_at=now,
            store_id=store_id,
            date=report_date,
            sessions=rows,
            total_opening_float=totals["opening_float"],
            total_cash_sales=totals["cash_sales"],
            total_acquisition_void_in=totals["acquisition_void_in"],
            total_buyout_out=totals["buyout_out"],
            total_consignment_payout_out=totals["consignment_payout_out"],
            total_sale_refund_out=totals["sale_refund_out"],
            total_manual_adjust=totals["manual_adjust"],
            total_expected=totals["expected"],
            total_counted=totals["counted"],
            total_variance=totals["variance"],
            total_store_credit_redeemed_display_only=sc_redeemed,
        )

    async def _sum_flows(
        self, store_id: int, start: datetime, end: datetime
    ) -> tuple[Decimal, Decimal]:
        """[start, end) 購物金發出/兌付 net 合計（經 flows；任意視窗）。"""
        rows = await self._sc.flows(store_id, date_from=start, date_to=end, granularity="day")
        issued = Decimal(0)
        redeemed = Decimal(0)
        for row in rows:
            i = row["issued"]
            r = row["redeemed"]
            assert isinstance(i, Decimal)
            assert isinstance(r, Decimal)
            issued += i
            redeemed += r
        return issued, redeemed

    async def trends(
        self, store_id: int, *, date_from: datetime, date_to: datetime, granularity: str
    ) -> TrendsReport:
        """財務趨勢時間序列（docs/19 R6）：依 granularity 分桶的 R5 同義 KPI；桶與 [from,to) 交集。

        各桶 KPI 由 margin_breakdown（毛利）、flows（購物金）、cash_out_in_range（現金支出）算得，
        與 R2/R5 同源；故各桶加總 = 全期 margin_breakdown，可交叉驗證。空桶補 0。
        """
        if granularity not in ("day", "week", "month", "quarter"):
            raise DomainError("granularity 僅支援 day/week/month/quarter")
        now = _now()
        rows: list[TrendRow] = []
        cursor, _ = _bucket_bounds(granularity, date_from)
        count = 0
        while cursor < date_to:
            _, nxt = _bucket_bounds(granularity, cursor)
            count += 1
            if count > MAX_TREND_BUCKETS:
                raise DomainError(
                    f"期間/粒度產生過多分桶（>{MAX_TREND_BUCKETS}）；請縮小區間或放大粒度"
                )
            bstart = max(cursor, date_from)
            bend = min(nxt, date_to)
            margin = await self._sales.margin_breakdown(store_id, bstart, bend)
            issued, redeemed = await self._sum_flows(store_id, bstart, bend)
            cash_out = await self._cash.cash_out_in_range(store_id, bstart, bend)
            rows.append(
                TrendRow(
                    period=cursor.date(),
                    gross_turnover=margin.gross_turnover,
                    recognized_revenue=margin.recognized_revenue,
                    food_revenue=margin.food_revenue,
                    secondhand_revenue=margin.secondhand_revenue,
                    gross_margin=margin.gross_margin,
                    gross_margin_rate=margin.gross_margin_rate,
                    cogs=margin.owned_cogs + margin.bulk_cogs,
                    total_cash_out=cash_out,
                    store_credit_issued=issued,
                    store_credit_redeemed=redeemed,
                    transaction_count=margin.transaction_count,
                )
            )
            cursor = nxt
        return TrendsReport(
            generated_at=now,
            store_id=store_id,
            date_from=date_from,
            date_to=date_to,
            granularity=granularity,
            rows=rows,
        )

    async def daily_summary(self, store_id: int, report_date: date) -> DailySummaryReport:
        """每日營運儀表板（docs/19 R5）：組合 daily_cash（R1）+ margin_breakdown（R2）的同源數字。

        稅以認列營收在總額層級推一次（§6）。估算淨利＝毛利 − 當日攤提固定支出，明確標註為估計
        （固定營業費用系統未逐日記錄）；月固定支出未設 → null。
        """
        now = _now()
        start = datetime(report_date.year, report_date.month, report_date.day, tzinfo=UTC)
        end = start + timedelta(days=1)
        cash = await self.daily_cash(store_id, report_date)
        margin = await self._sales.margin_breakdown(store_id, start, end)
        settings = await self._settings.get_effective_settings(store_id)

        flows = await self._sc.flows(store_id, date_from=start, date_to=end, granularity="day")
        sc_issued = Decimal(0)
        sc_redeemed = Decimal(0)
        for row in flows:
            issued = row["issued"]
            redeemed = row["redeemed"]
            assert isinstance(issued, Decimal)
            assert isinstance(redeemed, Decimal)
            sc_issued += issued
            sc_redeemed += redeemed

        net_ex_tax, tax = split_tax_inclusive(margin.recognized_revenue, settings.tax_rate)
        cogs = margin.owned_cogs + margin.bulk_cogs
        total_cash_out = cash.total_buyout_out + cash.total_consignment_payout_out
        avg_ticket: Decimal | None = (
            Decimal(round_ntd(margin.gross_turnover / Decimal(margin.transaction_count)))
            if margin.transaction_count > 0
            else None
        )

        days_in_month = calendar.monthrange(report_date.year, report_date.month)[1]
        monthly = settings.monthly_fixed_cash_outflow
        estimated_net_income: Decimal | None = (
            margin.gross_margin - Decimal(round_ntd(monthly / Decimal(days_in_month)))
            if monthly > 0
            else None
        )
        note = (
            "估算淨利＝毛利 − 當日攤提固定支出（月固定現金支出 ÷ 當月天數）；固定營業費用"
            "（租金/薪資）未逐日記錄，僅供概估、非精確損益。未設定月固定支出 → N/A。"
        )

        return DailySummaryReport(
            generated_at=now,
            store_id=store_id,
            date=report_date,
            gross_turnover=margin.gross_turnover,
            recognized_revenue=margin.recognized_revenue,
            net_sales_ex_tax=Decimal(net_ex_tax),
            tax=Decimal(tax),
            consignment_commission_income=margin.consignment_commission_income,
            cogs=cogs,
            gross_margin=margin.gross_margin,
            gross_margin_rate=margin.gross_margin_rate,
            unknown_cost_sales=margin.unknown_cost_sales,
            food_revenue=margin.food_revenue,
            secondhand_revenue=margin.secondhand_revenue,
            cash_sales_in=cash.total_cash_sales,
            acquisition_void_in=cash.total_acquisition_void_in,
            buyout_out=cash.total_buyout_out,
            consignment_payout_out=cash.total_consignment_payout_out,
            manual_adjust=cash.total_manual_adjust,
            total_cash_out=total_cash_out,
            expected_cash=cash.total_expected,
            counted_cash=cash.total_counted,
            cash_variance=cash.total_variance,
            store_credit_issued=sc_issued,
            store_credit_redeemed=sc_redeemed,
            transaction_count=margin.transaction_count,
            avg_ticket=avg_ticket,
            estimated_net_income=estimated_net_income,
            estimated_net_income_note=note,
        )

    async def liability(self, store_id: int) -> LiabilityReport:
        now = _now()
        aging = await self._sc.aging_report(store_id, now=now)
        buckets = aging["buckets"]
        assert isinstance(buckets, dict)
        balances = await self._sc.per_member_balances(store_id)
        per_member: list[MemberBalanceRow] = []
        for contact_id, balance in balances:
            contact = await self._contacts.get_contact(store_id, contact_id)
            per_member.append(
                MemberBalanceRow(
                    contact_id=contact_id,
                    name=contact.name if contact is not None else f"#{contact_id}",
                    balance=balance,
                )
            )
        total = aging["total_outstanding"]
        assert isinstance(total, Decimal)
        settings = await self._settings.get_effective_settings(store_id)
        return LiabilityReport(
            generated_at=now,
            store_id=store_id,
            total_outstanding=total,
            aging_buckets=AgingBuckets(
                lt_30d=buckets["lt_30d"],
                d30_90=buckets["d30_90"],
                d90_180=buckets["d90_180"],
                d180_365=buckets["d180_365"],
                gt_365d=buckets["gt_365d"],
            ),
            per_member=per_member,
            liability_health_ratio=_health_ratio(total, settings.monthly_fixed_cash_outflow),
        )

    async def flows(
        self,
        store_id: int,
        *,
        date_from: datetime,
        date_to: datetime,
        granularity: str,
    ) -> FlowsReport:
        now = _now()
        rows = await self._sc.flows(
            store_id, date_from=date_from, date_to=date_to, granularity=granularity
        )
        return FlowsReport(
            generated_at=now,
            store_id=store_id,
            granularity=granularity,
            date_from=date_from,
            date_to=date_to,
            rows=[
                FlowRow(
                    period=row["period"].date()
                    if isinstance(row["period"], datetime)
                    else row["period"],
                    issued=row["issued"],
                    redeemed=row["redeemed"],
                    net_change=row["net_change"],
                    issued_gross=row["issued_gross"],
                    issued_reversed=row["issued_reversed"],
                    redeemed_gross=row["redeemed_gross"],
                    redeemed_reversed=row["redeemed_reversed"],
                    adjustment_net=row["adjustment_net"],
                )
                for row in rows
            ],
        )

    async def effectiveness(
        self, store_id: int, *, date_from: datetime, date_to: datetime
    ) -> EffectivenessReport:
        """§5B 效益指標（單期間）；β/α/Δ 為估計值，估計欄位於 estimate_fields 標明。"""
        now = _now()
        pm = await self._suggestion.effectiveness(
            store_id, date_from=date_from, date_to=date_to, now=now
        )
        return EffectivenessReport(
            generated_at=now,
            store_id=store_id,
            date_from=date_from,
            date_to=date_to,
            take_rate=pm.take_rate,
            avg_premium_rate=pm.avg_premium_rate,
            beta_retention=pm.beta_retention,
            excess_spend_rate=pm.excess_spend_rate,
            alpha_incremental=pm.alpha_incremental,
            gross_margin_m=pm.gross_margin_m,
            delta_per_1000=pm.delta_per_1000,
            redemption_count=pm.redemption_count,
            alpha_sample_insufficient=pm.alpha_sample_insufficient,
            estimate_fields=ESTIMATE_FIELDS,
            alpha_method_note=ALPHA_METHOD_NOTE,
        )

    async def reconciliation(self, store_id: int) -> ReconciliationReport:
        now = _now()
        rec = await self._sc.reconcile(store_id)
        return ReconciliationReport(
            generated_at=now,
            store_id=store_id,
            mismatches=rec["mismatches"],
            ledger_total_outstanding=Decimal(str(rec["ledger_total_outstanding"])),
            cached_total_outstanding=Decimal(str(rec["cached_total_outstanding"])),
            cached_total_trustworthy=bool(rec["cached_total_trustworthy"]),
        )
