"""購物金建議引擎的台灣日曆視窗。"""

from datetime import UTC, datetime

from app.modules.storecredit.suggestion_service import _window_ranges


def test_windows_use_completed_taipei_calendar_days() -> None:
    now = datetime(2026, 7, 21, 16, 30, tzinfo=UTC)  # 台灣 2026-07-22 00:30

    ranges = _window_ranges(now, yoy_halfwidth_days=15)

    assert ranges["yesterday"] == (
        datetime(2026, 7, 20, 16, tzinfo=UTC),
        datetime(2026, 7, 21, 16, tzinfo=UTC),
    )
    assert ranges["d7"] == (
        datetime(2026, 7, 14, 16, tzinfo=UTC),
        datetime(2026, 7, 21, 16, tzinfo=UTC),
    )
    assert ranges["d30"] == (
        datetime(2026, 6, 21, 16, tzinfo=UTC),
        datetime(2026, 7, 21, 16, tzinfo=UTC),
    )
    assert ranges["d90"] == (
        datetime(2026, 4, 22, 16, tzinfo=UTC),
        datetime(2026, 7, 21, 16, tzinfo=UTC),
    )


def test_yoy_window_uses_same_taipei_calendar_date_and_includes_both_edges() -> None:
    now = datetime(2026, 7, 21, 16, 30, tzinfo=UTC)  # 台灣 2026-07-22

    start, end = _window_ranges(now, yoy_halfwidth_days=15)["yoy"]

    assert start == datetime(2025, 7, 6, 16, tzinfo=UTC)  # 台灣 2025-07-07 00:00
    assert end == datetime(2025, 8, 6, 16, tzinfo=UTC)  # 台灣 2025-08-07 00:00


def test_yoy_window_clamps_leap_day_to_february_28() -> None:
    now = datetime(2024, 2, 28, 16, 30, tzinfo=UTC)  # 台灣 2024-02-29

    start, end = _window_ranges(now, yoy_halfwidth_days=0)["yoy"]

    assert start == datetime(2023, 2, 27, 16, tzinfo=UTC)
    assert end == datetime(2023, 2, 28, 16, tzinfo=UTC)
