"""SC-4 購物金報表 service：彙整 storecredit / contacts service 的唯讀資料成報表。

只透過對方 service 取數（不直接碰他模組資料表，CLAUDE.md §2）；數值全部從帳本推導
（docs/16 §5），本層不寫任何資料。
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.contacts.service import ContactService
from app.modules.reports.schemas import (
    ALPHA_METHOD_NOTE,
    ESTIMATE_FIELDS,
    AgingBuckets,
    EffectivenessReport,
    FlowRow,
    FlowsReport,
    LiabilityReport,
    MemberBalanceRow,
    ReconciliationReport,
)
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
