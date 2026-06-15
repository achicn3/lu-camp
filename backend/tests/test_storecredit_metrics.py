"""§5B 效益指標純函數測試（docs/16 §5B）。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.modules.reports.aging import IssuedLot
from app.modules.storecredit.metrics import (
    alpha_ratio,
    delta_per_1000,
    is_new_leaning,
    member_aged_unredeemed,
    safe_ratio,
)

NOW = datetime(2026, 6, 15, tzinfo=UTC)


def _days_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


# ── safe_ratio ──


def test_safe_ratio_normal() -> None:
    assert safe_ratio(Decimal(3), Decimal(4)) == Decimal("0.75")


def test_safe_ratio_zero_denominator_is_none() -> None:
    assert safe_ratio(Decimal(5), Decimal(0)) is None
    assert safe_ratio(Decimal(5), Decimal(-1)) is None


# ── β 沉澱率（FIFO，滿 N 天）──


def test_aged_unredeemed_all_aged_unconsumed() -> None:
    lots = [IssuedLot(Decimal(100), _days_ago(200)), IssuedLot(Decimal(50), _days_ago(190))]
    unredeemed, total = member_aged_unredeemed(lots, Decimal(0), NOW, 180)
    assert unredeemed == Decimal(150)
    assert total == Decimal(150)


def test_aged_unredeemed_fifo_consumes_oldest_first() -> None:
    # 消耗 120：吃掉最舊 100 + 次舊 20；剩 30 在第二筆（仍滿 180 天）。
    lots = [IssuedLot(Decimal(100), _days_ago(200)), IssuedLot(Decimal(50), _days_ago(185))]
    unredeemed, total = member_aged_unredeemed(lots, Decimal(120), NOW, 180)
    assert unredeemed == Decimal(30)
    assert total == Decimal(150)


def test_aged_unredeemed_excludes_young_lots() -> None:
    # 只有第一筆滿 180 天；第二筆 100 天不計入分母。
    lots = [IssuedLot(Decimal(100), _days_ago(200)), IssuedLot(Decimal(50), _days_ago(100))]
    unredeemed, total = member_aged_unredeemed(lots, Decimal(0), NOW, 180)
    assert unredeemed == Decimal(100)
    assert total == Decimal(100)


def test_aged_unredeemed_no_aged_lots() -> None:
    lots = [IssuedLot(Decimal(100), _days_ago(10))]
    unredeemed, total = member_aged_unredeemed(lots, Decimal(0), NOW, 180)
    assert (unredeemed, total) == (Decimal(0), Decimal(0))


# ── α 代理分類 ──


def test_is_new_leaning_low_frequency() -> None:
    assert is_new_leaning(
        purchase_count=1,
        credit_issued_at=_days_ago(10),
        member_created_at=_days_ago(400),
        window_days=90,
    ) is True


def test_is_new_leaning_new_member() -> None:
    # 消費筆數足夠，但建檔距入帳 < 90 天 → 仍判新增傾向高。
    assert is_new_leaning(
        purchase_count=5,
        credit_issued_at=_days_ago(10),
        member_created_at=_days_ago(40),
        window_days=90,
    ) is True


def test_is_new_leaning_established_member() -> None:
    assert is_new_leaning(
        purchase_count=5,
        credit_issued_at=_days_ago(10),
        member_created_at=_days_ago(400),
        window_days=90,
    ) is False


def test_alpha_ratio() -> None:
    classified = [(Decimal(100), True), (Decimal(300), False), (Decimal(100), True)]
    assert alpha_ratio(classified) == Decimal("0.4")  # 200 / 500


def test_alpha_ratio_no_redemptions_is_none() -> None:
    assert alpha_ratio([]) is None


# ── Δ/1000 ──


def test_delta_per_1000() -> None:
    # β=0.5, p=0.1, α=0.4, m=0.5 → 1−(0.5)(1.1)(1−0.2)=1−0.44=0.56 → 560
    result = delta_per_1000(
        beta=Decimal("0.5"), avg_premium=Decimal("0.1"), alpha=Decimal("0.4"), margin=Decimal("0.5")
    )
    assert result == Decimal("560.000")


def test_delta_per_1000_missing_input_is_none() -> None:
    assert delta_per_1000(
        beta=None, avg_premium=Decimal("0.1"), alpha=Decimal("0.4"), margin=Decimal("0.5")
    ) is None
