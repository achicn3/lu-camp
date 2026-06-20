"""SC-4 購物金報表 service：彙整 storecredit / contacts service 的唯讀資料成報表。

只透過對方 service 取數（不直接碰他模組資料表，CLAUDE.md §2）；數值全部從帳本推導
（docs/16 §5），本層不寫任何資料。
"""

import calendar
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import round_ntd, split_tax_inclusive
from app.modules.cashdrawer.service import CashDrawerService
from app.modules.contacts.service import ContactService
from app.modules.reports.schemas import (
    ALPHA_METHOD_NOTE,
    ESTIMATE_FIELDS,
    AgingBuckets,
    DailyCashReport,
    DailyCashSessionRow,
    DailySummaryReport,
    EffectivenessReport,
    FlowRow,
    FlowsReport,
    LiabilityReport,
    MemberBalanceRow,
    ReconciliationReport,
    SalesMarginReport,
)
from app.modules.sales.service import SalesService
from app.modules.settings.service import StoreSettingsService
from app.modules.storecredit.service import StoreCreditService
from app.modules.storecredit.suggestion_service import PremiumSuggestionService


def _now() -> datetime:
    return datetime.now(UTC)


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
            cash_received=bd.cash_received,
            store_credit_redeemed=bd.store_credit_redeemed,
            transaction_count=bd.transaction_count,
        )

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
