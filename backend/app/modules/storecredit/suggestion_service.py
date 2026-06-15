"""SC-5b 溢價建議 orchestrate（docs/16 §5B/§6.2）：組各視窗指標 → 純引擎 → lazy 落庫。

定位：高層協調者，跨模組經對方 service 取數（acquisition/sales/contacts/settings；§2）。
**不可**併入 StoreCreditService——acquisition/sales service 已 import 它，反向 import 會循環。
本店自身的帳本指標與建議落庫走 StoreCreditRepository（同模組）。

α 一律代理法估計（§5B-α）；β 為「至今仍未沖銷」之點時值（視窗無關，各視窗共用同值）。
建議值永不自動生效——僅計算、落庫、回傳供人工確認。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.acquisition.service import AcquisitionService
from app.modules.contacts.service import ContactService
from app.modules.reports.aging import IssuedLot
from app.modules.sales.service import SalesService
from app.modules.settings.service import StoreSettingsService
from app.modules.storecredit.engine import (
    WINDOW_NAMES,
    EngineParams,
    EngineResult,
    WindowMetrics,
    suggest_premium_rate,
)
from app.modules.storecredit.metrics import (
    PeriodMetrics,
    alpha_ratio,
    delta_per_1000,
    is_new_leaning,
    member_aged_unredeemed,
    safe_ratio,
)
from app.modules.storecredit.models import StoreCreditSuggestionLog
from app.modules.storecredit.repository import StoreCreditRepository
from app.shared.enums import PayoutMethod


def _window_ranges(now: datetime, yoy_halfwidth_days: int) -> dict[str, tuple[datetime, datetime]]:
    """各回看視窗的 [from, to)（docs/16 §6.2）：昨日/近7/30/90天/去年同期±halfwidth。"""
    yoy_center = now - timedelta(days=365)
    return {
        "yesterday": (now - timedelta(days=1), now),
        "d7": (now - timedelta(days=7), now),
        "d30": (now - timedelta(days=30), now),
        "d90": (now - timedelta(days=90), now),
        "yoy": (
            yoy_center - timedelta(days=yoy_halfwidth_days),
            yoy_center + timedelta(days=yoy_halfwidth_days),
        ),
    }


def _metrics_to_dict(pm: PeriodMetrics) -> dict[str, Any]:
    """PeriodMetrics → JSONB 友善 dict（比率轉字串保精度；None 保留）。"""
    return {
        "take_rate": _s(pm.take_rate),
        "avg_premium_rate": _s(pm.avg_premium_rate),
        "beta_retention": _s(pm.beta_retention),
        "excess_spend_rate": _s(pm.excess_spend_rate),
        "alpha_incremental": _s(pm.alpha_incremental),
        "gross_margin_m": _s(pm.gross_margin_m),
        "delta_per_1000": _s(pm.delta_per_1000),
        "redemption_count": pm.redemption_count,
        "alpha_sample_insufficient": pm.alpha_sample_insufficient,
    }


def _s(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


class PremiumSuggestionService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = StoreCreditRepository(session)
        self._sales = SalesService(session)
        self._acquisitions = AcquisitionService(session)
        self._contacts = ContactService(session)
        self._settings = StoreSettingsService(session)

    # ── 對外：當日建議（lazy 計算 + 冪等落庫）與單期效益報表 ──

    async def suggestion_today(
        self, store_id: int, *, today: date, now: datetime
    ) -> StoreCreditSuggestionLog:
        """當日建議值：已有當日 log 直接回；否則計算 → 落庫（撞唯一鍵回既有，冪等）。"""
        existing = await self._repo.get_suggestion_log(store_id, today)
        if existing is not None:
            return existing
        window_metrics, result = await self._compute_suggestion(store_id, now=now)
        log = StoreCreditSuggestionLog(
            store_id=store_id,
            for_date=today,
            window_metrics=window_metrics,
            constraint_values=result.constraint_values,
            suggested_rate=result.suggested_rate,
            engine_version=result.engine_version,
            insufficient_data=result.insufficient_data,
        )
        try:
            async with self._session.begin_nested():
                await self._repo.add_suggestion_log(log)
        except IntegrityError:
            # 並發首讀撞每店每日唯一鍵：純函數＋同日同輸入 → 既有列即正解，重查回傳。
            again = await self._repo.get_suggestion_log(store_id, today)
            assert again is not None
            return again
        return log

    async def effectiveness(
        self, store_id: int, *, date_from: datetime, date_to: datetime, now: datetime
    ) -> PeriodMetrics:
        """§5B 效益指標（單期間）：take/avg_premium/excess/margin/α/β/Δ（β 為點時值）。"""
        params = await self._engine_params(store_id)
        beta = await self._compute_beta(store_id, now=now, n_days=params.beta_n_days)
        return await self._compute_period_metrics(
            store_id, date_from=date_from, date_to=date_to, params=params, beta=beta
        )

    # ── 引擎輸入組裝 ──

    async def _compute_suggestion(
        self, store_id: int, *, now: datetime
    ) -> tuple[dict[str, Any], EngineResult]:
        settings = await self._settings.get_effective_settings(store_id)
        params = EngineParams.from_mapping(settings.store_credit_engine_params)
        beta = await self._compute_beta(store_id, now=now, n_days=params.beta_n_days)

        ranges = _window_ranges(now, params.yoy_halfwidth_days)
        windows: dict[str, WindowMetrics | None] = {}
        window_detail: dict[str, Any] = {}
        for name in WINDOW_NAMES:
            date_from, date_to = ranges[name]
            pm = await self._compute_period_metrics(
                store_id, date_from=date_from, date_to=date_to, params=params, beta=beta
            )
            windows[name] = pm.to_window_metrics()
            window_detail[name] = _metrics_to_dict(pm)

        monthly_outflow = Decimal(settings.monthly_fixed_cash_outflow)
        total_outstanding = await self._repo.total_outstanding(store_id)
        liability_ratio = (
            total_outstanding / monthly_outflow if monthly_outflow > 0 else None
        )
        data_days = await self._data_days(store_id, now=now)

        result = suggest_premium_rate(
            windows=windows,
            current_rate=settings.premium_rate,
            premium_min=settings.premium_rate_min,
            premium_max=settings.premium_rate_max,
            liability_ratio=liability_ratio,
            params=params,
            data_days=data_days,
        )
        window_metrics_json: dict[str, Any] = {
            "windows": window_detail,
            "combined": result.combined_metrics,
            "normalized_weights": result.normalized_weights,
            "liability_ratio": _s(liability_ratio),
            "data_days": data_days,
            "current_rate": str(settings.premium_rate),
            "premium_rate_min": str(settings.premium_rate_min),
            "premium_rate_max": str(settings.premium_rate_max),
        }
        return window_metrics_json, result

    async def _engine_params(self, store_id: int) -> EngineParams:
        settings = await self._settings.get_effective_settings(store_id)
        return EngineParams.from_mapping(settings.store_credit_engine_params)

    async def _data_days(self, store_id: int, *, now: datetime) -> int:
        earliest = await self._repo.earliest_activity_at(store_id)
        if earliest is None:
            return 0
        return (now - earliest).days

    # ── 單期間指標 ──

    async def _compute_period_metrics(
        self,
        store_id: int,
        *,
        date_from: datetime,
        date_to: datetime,
        params: EngineParams,
        beta: Decimal | None,
    ) -> PeriodMetrics:
        # take_rate：選購物金（含 SPLIT）÷ 全部收購筆數。
        counts = await self._acquisitions.count_payouts_by_method(store_id, date_from, date_to)
        credit_count = counts.get(PayoutMethod.STORE_CREDIT, 0) + counts.get(PayoutMethod.SPLIT, 0)
        total_count = sum(counts.values())
        take_rate = safe_ratio(Decimal(credit_count), Decimal(total_count))

        # avg_premium_rate：Σ(signed−cash) ÷ Σ cash（CREDIT 列）。
        sum_signed, sum_cash = await self._repo.credit_premium_components(
            store_id, date_from, date_to
        )
        avg_premium = safe_ratio(sum_signed - sum_cash, sum_cash)

        # excess_spend_rate：含購物金 tender 銷售的現金部分 ÷ total。
        es = await self._sales.excess_spend_components(store_id, date_from, date_to)
        excess_spend = safe_ratio(es["cash"], es["total"])

        # gross_margin_m：（買斷毛利＋寄售抽成）÷ 商品收入。
        pm = await self._sales.period_margin(store_id, date_from, date_to)
        margin = safe_ratio(
            pm["buyout_margin"] + pm["consignment_commission"], pm["revenue"]
        )

        # alpha_incremental：代理法（§5B-α）。
        alpha, redemption_count = await self._compute_alpha(
            store_id,
            date_from=date_from,
            date_to=date_to,
            window_days=params.alpha_proxy_window_days,
        )

        delta = delta_per_1000(beta=beta, avg_premium=avg_premium, alpha=alpha, margin=margin)
        return PeriodMetrics(
            take_rate=take_rate,
            avg_premium_rate=avg_premium,
            beta_retention=beta,
            excess_spend_rate=excess_spend,
            alpha_incremental=alpha,
            gross_margin_m=margin,
            delta_per_1000=delta,
            redemption_count=redemption_count,
        )

    async def _compute_alpha(
        self, store_id: int, *, date_from: datetime, date_to: datetime, window_days: int
    ) -> tuple[Decimal | None, int]:
        """α 代理（§5B-α）：期間每筆兌付，依「對應 CREDIT 入帳前 N 天消費筆數 / 會員資歷」分類。"""
        debits = await self._repo.debits_in_period(store_id, date_from, date_to)
        if not debits:
            return None, 0
        contact_ids = sorted({contact_id for contact_id, _ in debits})
        earliest_credit = await self._repo.earliest_credit_at_by_contacts(store_id, contact_ids)

        # 每個會員只查一次（同期間多筆兌付共用分類）。
        new_leaning_by_contact: dict[int, bool] = {}
        for contact_id in contact_ids:
            ref = earliest_credit.get(contact_id)
            if ref is None:
                new_leaning_by_contact[contact_id] = False  # 無 CREDIT（理論上不該發生）→ 視為既有
                continue
            contact = await self._contacts.get_contact(store_id, contact_id)
            member_created = contact.created_at if contact is not None else ref
            purchase_count = await self._sales.member_purchase_count(
                store_id,
                contact_id,
                date_from=ref - timedelta(days=window_days),
                date_to=ref,
            )
            new_leaning_by_contact[contact_id] = is_new_leaning(
                purchase_count=purchase_count,
                credit_issued_at=ref,
                member_created_at=member_created,
                window_days=window_days,
            )
        classified = [(amount, new_leaning_by_contact[contact_id]) for contact_id, amount in debits]
        return alpha_ratio(classified), len(debits)

    async def _compute_beta(
        self, store_id: int, *, now: datetime, n_days: int
    ) -> Decimal | None:
        """β 沉澱率（點時值）：發出滿 n_days 的 CREDIT 中，FIFO 後仍未沖銷的金額比例。"""
        lots_rows = await self._repo.credit_lots(store_id)
        positive_sum = await self._repo.positive_sum_by_contact(store_id)
        balances = await self._repo.balances_by_contact(store_id)
        per_contact: dict[int, list[IssuedLot]] = defaultdict(list)
        for contact_id, amount, issued_at in lots_rows:
            per_contact[contact_id].append(IssuedLot(amount=amount, issued_at=issued_at))

        aged_unredeemed = Decimal(0)
        aged_total = Decimal(0)
        for contact_id, lots in per_contact.items():
            balance = balances.get(contact_id, Decimal(0))
            consumed = positive_sum.get(contact_id, Decimal(0)) - balance
            unredeemed, total = member_aged_unredeemed(lots, consumed, now, n_days)
            aged_unredeemed += unredeemed
            aged_total += total
        return safe_ratio(aged_unredeemed, aged_total)
