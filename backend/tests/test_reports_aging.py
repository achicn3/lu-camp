"""購物金帳齡 FIFO 分桶純函數測試（SC-4，docs/16 §5A）。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.modules.reports.aging import IssuedLot, age_outstanding

NOW = datetime(2026, 6, 14, tzinfo=UTC)


def _lot(amount: int, days_ago: int) -> IssuedLot:
    return IssuedLot(amount=Decimal(amount), issued_at=NOW - timedelta(days=days_ago))


def test_no_consumption_buckets_by_age() -> None:
    lots = [_lot(100, 10), _lot(200, 60), _lot(300, 200), _lot(400, 400)]
    buckets = age_outstanding(lots, Decimal(0), NOW)
    assert buckets["lt_30d"] == Decimal(100)
    assert buckets["d30_90"] == Decimal(200)
    assert buckets["d180_365"] == Decimal(300)
    assert buckets["gt_365d"] == Decimal(400)
    assert sum(buckets.values()) == Decimal(1000)


def test_fifo_consumes_oldest_first() -> None:
    # 發出 100(老) + 100(新)，已消耗 150 → 老的全沒、新的剩 50
    lots = [_lot(100, 100), _lot(100, 10)]
    buckets = age_outstanding(lots, Decimal(150), NOW)
    assert buckets["lt_30d"] == Decimal(50)
    assert sum(buckets.values()) == Decimal(50)


def test_consume_all_yields_empty() -> None:
    lots = [_lot(100, 100), _lot(100, 10)]
    buckets = age_outstanding(lots, Decimal(500), NOW)  # 超額消耗夾住
    assert sum(buckets.values()) == Decimal(0)


def test_boundary_30_days_goes_to_higher_bucket() -> None:
    # 恰 30 天 → 不屬 <30，落 30–90
    buckets = age_outstanding([_lot(100, 30)], Decimal(0), NOW)
    assert buckets["lt_30d"] == Decimal(0)
    assert buckets["d30_90"] == Decimal(100)
