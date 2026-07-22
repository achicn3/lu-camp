"""門市時間規則：瞬間存 UTC，營業日期固定使用 Asia/Taipei。"""

from datetime import UTC, date, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from app.core.time import (
    AwareDateTime,
    store_bucket_bounds,
    store_date,
    store_datetime_iso,
    store_day_bounds,
)


def test_store_day_bounds_converts_taipei_midnight_to_utc_half_open_range() -> None:
    start, end = store_day_bounds(date(2026, 7, 22))

    assert start == datetime(2026, 7, 21, 16, tzinfo=UTC)
    assert end == datetime(2026, 7, 22, 16, tzinfo=UTC)


def test_store_date_changes_at_taipei_midnight_not_utc_midnight() -> None:
    assert store_date(datetime(2026, 7, 21, 15, 59, 59, tzinfo=UTC)) == date(2026, 7, 21)
    assert store_date(datetime(2026, 7, 21, 16, tzinfo=UTC)) == date(2026, 7, 22)


def test_store_date_rejects_datetime_without_timezone() -> None:
    with pytest.raises(ValueError, match="日期時間必須包含時區"):
        store_date(datetime(2026, 7, 22, 0, 0))


@pytest.mark.parametrize(
    ("granularity", "expected_start", "expected_end"),
    [
        ("day", datetime(2026, 7, 21, 16, tzinfo=UTC), datetime(2026, 7, 22, 16, tzinfo=UTC)),
        ("week", datetime(2026, 7, 19, 16, tzinfo=UTC), datetime(2026, 7, 26, 16, tzinfo=UTC)),
        ("month", datetime(2026, 6, 30, 16, tzinfo=UTC), datetime(2026, 7, 31, 16, tzinfo=UTC)),
        ("quarter", datetime(2026, 6, 30, 16, tzinfo=UTC), datetime(2026, 9, 30, 16, tzinfo=UTC)),
    ],
)
def test_store_bucket_bounds_align_to_taipei_calendar(
    granularity: str, expected_start: datetime, expected_end: datetime
) -> None:
    start, end = store_bucket_bounds(granularity, datetime(2026, 7, 22, 3, tzinfo=UTC))

    assert (start, end) == (expected_start, expected_end)


def test_store_bucket_bounds_rejects_datetime_without_timezone() -> None:
    with pytest.raises(ValueError, match="日期時間必須包含時區"):
        store_bucket_bounds("day", datetime(2026, 7, 22, 0, 0))


def test_aware_datetime_rejects_missing_offset_and_normalizes_to_utc() -> None:
    adapter: TypeAdapter[datetime] = TypeAdapter(AwareDateTime)

    with pytest.raises(ValidationError):
        adapter.validate_python("2026-07-22T00:00:00")
    assert adapter.validate_python("2026-07-22T00:00:00+08:00") == datetime(
        2026, 7, 21, 16, tzinfo=UTC
    )


def test_store_datetime_iso_uses_taipei_offset() -> None:
    assert store_datetime_iso(datetime(2026, 7, 21, 16, 30, tzinfo=UTC)) == (
        "2026-07-22T00:30:00+08:00"
    )
