"""§5B 效益指標的純函數核心（docs/16 §5B）。

把「從帳本/銷售/收購撈出的原始量」轉成效益比率——無 DB 依賴、可單元測試。
service 層負責跨模組取數（§2），把數字餵進這些純函數；α 一律代理法估計、標示估計值。

包含：safe_ratio（共用）、member_aged_unredeemed（β 沉澱率的 FIFO 計算）、
is_new_leaning＋alpha_ratio（α 代理分類）、delta_per_1000（損益敏感度 Δ）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.modules.reports.aging import IssuedLot
from app.modules.storecredit.engine import WindowMetrics

# α 代理法的「樣本不足」門檻：期間兌付筆數低於此值時 UI/報表加註（docs/16 §5B-α）。
ALPHA_MIN_SAMPLE = 30
# α 代理分類：消費筆數低於此值視為「新增傾向高」之一條件（docs/16 §5B-α）。
_LOW_FREQUENCY_PURCHASES = 2


@dataclass(frozen=True)
class PeriodMetrics:
    """單一期間/視窗的 §5B 全指標（docs/16 §5B）。比率欄缺樣本以 None 表示。

    take_rate/avg_premium_rate/excess_spend_rate/gross_margin_m 為直接量測；
    beta_retention/alpha_incremental/delta_per_1000 為估計值（UI/報表須標示）。
    redemption_count 為期間兌付筆數（< ALPHA_MIN_SAMPLE 時 α 加註「樣本不足」）。
    """

    take_rate: Decimal | None
    avg_premium_rate: Decimal | None
    beta_retention: Decimal | None
    excess_spend_rate: Decimal | None
    alpha_incremental: Decimal | None
    gross_margin_m: Decimal | None
    delta_per_1000: Decimal | None
    redemption_count: int

    def to_window_metrics(self) -> WindowMetrics:
        """取引擎所需的子集（§6.2 綜合指標用：take/avg_premium/beta/alpha/margin）。"""
        return WindowMetrics(
            take_rate=self.take_rate,
            avg_premium_rate=self.avg_premium_rate,
            beta_retention=self.beta_retention,
            alpha_incremental=self.alpha_incremental,
            gross_margin_m=self.gross_margin_m,
        )

    @property
    def alpha_sample_insufficient(self) -> bool:
        return self.redemption_count < ALPHA_MIN_SAMPLE


def safe_ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """numerator ÷ denominator；分母 ≤ 0（無樣本）→ None（呼叫端據此標示 N/A）。"""
    if denominator <= 0:
        return None
    return numerator / denominator


def member_aged_unredeemed(
    lots: list[IssuedLot], consumed_total: Decimal, now: datetime, n_days: int
) -> tuple[Decimal, Decimal]:
    """單一會員：FIFO 沖銷後，發出滿 n_days 的 CREDIT 之（未沖銷額, 該批總額）。

    β 沉澱率分子/分母用：分母 = 滿 N 天的 CREDIT 總額；分子 = 其中仍未被 FIFO 沖銷者。
    沖銷由最舊發出列開始（與帳齡分桶一致）；consumed_total 為非負已消耗額。
    """
    remaining_to_consume = consumed_total if consumed_total > 0 else Decimal(0)
    aged_unredeemed = Decimal(0)
    aged_total = Decimal(0)
    for lot in sorted(lots, key=lambda lot_: lot_.issued_at):
        lot_remaining = lot.amount
        if remaining_to_consume > 0:
            take = min(lot_remaining, remaining_to_consume)
            lot_remaining -= take
            remaining_to_consume -= take
        age_days = (now - lot.issued_at).total_seconds() / 86400
        if age_days >= n_days:
            aged_total += lot.amount
            if lot_remaining > 0:
                aged_unredeemed += lot_remaining
    return aged_unredeemed, aged_total


def is_new_leaning(
    *,
    purchase_count: int,
    credit_issued_at: datetime,
    member_created_at: datetime,
    window_days: int,
) -> bool:
    """α 代理分類（docs/16 §5B-α）：消費筆數 < 2，或會員建檔距入帳 < window_days → 新增傾向高。"""
    if purchase_count < _LOW_FREQUENCY_PURCHASES:
        return True
    tenure_days = (credit_issued_at - member_created_at).total_seconds() / 86400
    return tenure_days < window_days


def alpha_ratio(classified: list[tuple[Decimal, bool]]) -> Decimal | None:
    """α 估計 = 新增傾向高之兌付金額 ÷ 全部兌付金額；無兌付 → None。"""
    total = sum((amount for amount, _ in classified), Decimal(0))
    if total <= 0:
        return None
    new_leaning = sum((amount for amount, is_new in classified if is_new), Decimal(0))
    return new_leaning / total


def delta_per_1000(
    *,
    beta: Decimal | None,
    avg_premium: Decimal | None,
    alpha: Decimal | None,
    margin: Decimal | None,
) -> Decimal | None:
    """損益敏感度 Δ/1000 = 1000 × [1 − (1−β)(1+p)(1−α·m)]（docs/16 §5B）；任一輸入缺 → None。"""
    if beta is None or avg_premium is None or alpha is None or margin is None:
        return None
    factor = (Decimal(1) - beta) * (Decimal(1) + avg_premium) * (Decimal(1) - alpha * margin)
    return Decimal(1000) * (Decimal(1) - factor)
