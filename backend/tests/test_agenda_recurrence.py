"""行事曆重複規則（docs/99 §4.2）：純函式，用明確日期、不依賴 now()。

邊界：29–31 號遇到沒有這天的月份落到月底；2/29 遇平年落到 2/28；每幾個月從開始月份起算；
開始日之前、結束日之後不算；結束日當天算。
"""

from datetime import date

import pytest

from app.modules.agenda.recurrence import (
    Recurrence,
    is_occurrence,
    occurrences_between,
    validate_recurrence,
)
from app.shared.enums import RecurrenceKind
from app.shared.exceptions import InvalidRecurrence


def _weekly(*weekdays: int, starts_on: date, ends_on: date | None = None) -> Recurrence:
    return Recurrence(
        kind=RecurrenceKind.WEEKLY, starts_on=starts_on, ends_on=ends_on, weekdays=weekdays
    )


def _monthly(day: int, *, starts_on: date, ends_on: date | None = None) -> Recurrence:
    return Recurrence(
        kind=RecurrenceKind.MONTHLY, starts_on=starts_on, ends_on=ends_on, day_of_month=day
    )


def test_weekly_lists_each_chosen_weekday_in_order() -> None:
    # 週一（0）、週四（3）叫牛奶；2026-11-01 是週日
    rule = _weekly(0, 3, starts_on=date(2026, 11, 1))
    assert occurrences_between(rule, date(2026, 11, 1), date(2026, 11, 14)) == [
        date(2026, 11, 2),
        date(2026, 11, 5),
        date(2026, 11, 9),
        date(2026, 11, 12),
    ]


def test_weekly_respects_start_and_inclusive_end() -> None:
    rule = _weekly(0, starts_on=date(2026, 11, 3), ends_on=date(2026, 11, 16))
    # 11/2 在開始日之前不算；11/16 是結束日當天，要算；11/23 在結束日之後
    assert occurrences_between(rule, date(2026, 11, 1), date(2026, 11, 30)) == [
        date(2026, 11, 9),
        date(2026, 11, 16),
    ]


def test_monthly_31st_falls_back_to_month_end() -> None:
    rule = _monthly(31, starts_on=date(2026, 11, 1))
    assert occurrences_between(rule, date(2026, 11, 1), date(2027, 3, 31)) == [
        date(2026, 11, 30),
        date(2026, 12, 31),
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
    ]


def test_monthly_29th_uses_leap_day_only_in_leap_years() -> None:
    rule = _monthly(29, starts_on=date(2027, 1, 1))
    found = occurrences_between(rule, date(2027, 2, 1), date(2028, 2, 29))
    assert date(2027, 2, 28) in found
    assert date(2028, 2, 29) in found


def test_monthly_skips_the_starting_month_when_its_day_already_passed() -> None:
    # 11/10 才開始、每月 5 號 → 第一次是 12/5，不會冒出一個早於開始日的 11/5
    rule = _monthly(5, starts_on=date(2026, 11, 10))
    assert occurrences_between(rule, date(2026, 11, 1), date(2027, 1, 31)) == [
        date(2026, 12, 5),
        date(2027, 1, 5),
    ]


def test_every_n_months_counts_from_the_starting_month() -> None:
    # 每 2 個月的 10 號交發票給記帳士，從 2026-11-15 開始：11/10 已過 → 1/10、3/10、5/10
    rule = Recurrence(
        kind=RecurrenceKind.EVERY_N_MONTHS,
        starts_on=date(2026, 11, 15),
        day_of_month=10,
        interval_months=2,
    )
    assert occurrences_between(rule, date(2026, 11, 1), date(2027, 5, 31)) == [
        date(2027, 1, 10),
        date(2027, 3, 10),
        date(2027, 5, 10),
    ]


def test_every_n_months_query_window_far_after_start_keeps_the_cadence() -> None:
    rule = Recurrence(
        kind=RecurrenceKind.EVERY_N_MONTHS,
        starts_on=date(2026, 1, 1),
        day_of_month=15,
        interval_months=3,
    )
    # 1、4、7、10 月 → 2027 年也是 1、4、7、10 月
    assert occurrences_between(rule, date(2027, 1, 1), date(2027, 12, 31)) == [
        date(2027, 1, 15),
        date(2027, 4, 15),
        date(2027, 7, 15),
        date(2027, 10, 15),
    ]


def test_yearly_leap_day_falls_back_to_feb_28() -> None:
    rule = Recurrence(
        kind=RecurrenceKind.YEARLY,
        starts_on=date(2027, 1, 1),
        month_of_year=2,
        day_of_month=29,
    )
    assert occurrences_between(rule, date(2027, 1, 1), date(2029, 12, 31)) == [
        date(2027, 2, 28),
        date(2028, 2, 29),
        date(2029, 2, 28),
    ]


def test_yearly_plain_date() -> None:
    rule = Recurrence(
        kind=RecurrenceKind.YEARLY,
        starts_on=date(2026, 10, 30),
        month_of_year=3,
        day_of_month=1,
    )
    assert occurrences_between(rule, date(2026, 1, 1), date(2028, 12, 31)) == [
        date(2027, 3, 1),
        date(2028, 3, 1),
    ]


def test_empty_when_window_is_before_start_or_inverted() -> None:
    rule = _weekly(0, starts_on=date(2026, 11, 1))
    assert occurrences_between(rule, date(2026, 10, 1), date(2026, 10, 31)) == []
    assert occurrences_between(rule, date(2026, 11, 30), date(2026, 11, 1)) == []


def test_long_history_is_not_silently_truncated() -> None:
    """Codex 1A 第三輪 P2：以前的日子要全部算到；每天最多一次，所以筆數不會超過天數。"""
    every_day = _weekly(0, 1, 2, 3, 4, 5, 6, starts_on=date(2020, 1, 1))
    found = occurrences_between(every_day, date(2020, 1, 1), date(2026, 11, 9))
    assert len(found) == (date(2026, 11, 9) - date(2020, 1, 1)).days + 1
    assert found[-1] == date(2026, 11, 9)


def test_explicit_limit_still_applies() -> None:
    """找「下一次」只要第一個：明確給上限時照上限停。"""
    every_day = _weekly(0, 1, 2, 3, 4, 5, 6, starts_on=date(2020, 1, 1))
    assert occurrences_between(every_day, date(2026, 11, 10), date(2027, 11, 10), limit=1) == [
        date(2026, 11, 10)
    ]


def test_is_occurrence() -> None:
    rule = _monthly(31, starts_on=date(2026, 11, 1))
    assert is_occurrence(rule, date(2026, 11, 30)) is True
    assert is_occurrence(rule, date(2026, 11, 29)) is False
    assert is_occurrence(rule, date(2026, 10, 31)) is False  # 開始日之前


def test_validate_accepts_and_normalizes_weekdays() -> None:
    rule = validate_recurrence(_weekly(3, 0, 3, starts_on=date(2026, 11, 1)))
    assert rule.weekdays == (0, 3)


@pytest.mark.parametrize(
    "rule",
    [
        # 每週沒選星期、星期超出 0–6
        Recurrence(kind=RecurrenceKind.WEEKLY, starts_on=date(2026, 11, 1)),
        Recurrence(kind=RecurrenceKind.WEEKLY, starts_on=date(2026, 11, 1), weekdays=(7,)),
        # 每月幾號超出 1–31、沒填
        Recurrence(kind=RecurrenceKind.MONTHLY, starts_on=date(2026, 11, 1), day_of_month=0),
        Recurrence(kind=RecurrenceKind.MONTHLY, starts_on=date(2026, 11, 1), day_of_month=32),
        Recurrence(kind=RecurrenceKind.MONTHLY, starts_on=date(2026, 11, 1)),
        # 每幾個月：間隔要 2–24（1 就是每月）
        Recurrence(
            kind=RecurrenceKind.EVERY_N_MONTHS,
            starts_on=date(2026, 11, 1),
            day_of_month=1,
            interval_months=1,
        ),
        Recurrence(
            kind=RecurrenceKind.EVERY_N_MONTHS,
            starts_on=date(2026, 11, 1),
            day_of_month=1,
            interval_months=25,
        ),
        # 每年：不存在的日期（2/30、4/31）
        Recurrence(
            kind=RecurrenceKind.YEARLY,
            starts_on=date(2026, 11, 1),
            month_of_year=2,
            day_of_month=30,
        ),
        Recurrence(
            kind=RecurrenceKind.YEARLY,
            starts_on=date(2026, 11, 1),
            month_of_year=4,
            day_of_month=31,
        ),
        Recurrence(
            kind=RecurrenceKind.YEARLY,
            starts_on=date(2026, 11, 1),
            month_of_year=13,
            day_of_month=1,
        ),
        # 結束日早於開始日
        Recurrence(
            kind=RecurrenceKind.MONTHLY,
            starts_on=date(2026, 11, 1),
            ends_on=date(2026, 10, 31),
            day_of_month=1,
        ),
        # 不屬於這種規則的欄位：畫面送錯就擋，不默默忽略
        Recurrence(
            kind=RecurrenceKind.MONTHLY,
            starts_on=date(2026, 11, 1),
            day_of_month=1,
            weekdays=(0,),
        ),
        Recurrence(
            kind=RecurrenceKind.WEEKLY,
            starts_on=date(2026, 11, 1),
            weekdays=(0,),
            day_of_month=1,
        ),
    ],
)
def test_validate_rejects_invalid_rules(rule: Recurrence) -> None:
    with pytest.raises(InvalidRecurrence):
        validate_recurrence(rule)
