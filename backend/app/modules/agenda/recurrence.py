"""行事曆重複規則（docs/99 §4.2、ADR-034 決定 6）：純函式，無 I/O。

只存規則、不預先展開：需要哪一段日期就算哪一段。
- 每週：選星期幾（0＝週一 … 6＝週日，同 `date.weekday()`）。
- 每月／每 N 個月：幾號；29–31 號遇到沒有這天的月份落到月底。每 N 個月從開始的月份起算。
- 每年：幾月幾號；2/29 遇平年落到 2/28。
開始日之前、結束日之後都不算；結束日當天算。
"""

import calendar
from dataclasses import dataclass, replace
from datetime import date, timedelta

from app.shared.enums import RecurrenceKind
from app.shared.exceptions import InvalidRecurrence

# 一次最多列幾個發生日：防呆上限（每週七天都選也要五年以上才碰到），不是業務規則。
MAX_OCCURRENCES = 2000
# 「每 N 個月」的 N：1 就是每月（用 MONTHLY），上限兩年。
MIN_INTERVAL_MONTHS = 2
MAX_INTERVAL_MONTHS = 24
# 驗證「每年幾月幾號」存不存在時用閏年，讓 2/29 合法（平年再落到 2/28）。
_LEAP_YEAR = 2000


@dataclass(frozen=True)
class Recurrence:
    """一條重複規則。只填該種規則用得到的欄位（`validate_recurrence` 會擋多填的）。"""

    kind: RecurrenceKind
    starts_on: date
    ends_on: date | None = None
    weekdays: tuple[int, ...] = ()
    day_of_month: int | None = None
    interval_months: int | None = None
    month_of_year: int | None = None


def validate_recurrence(rule: Recurrence) -> Recurrence:
    """檢查規則並回傳正規化後的版本（星期去重排序）。不合法拋 `InvalidRecurrence`。"""
    if rule.ends_on is not None and rule.ends_on < rule.starts_on:
        raise InvalidRecurrence("結束日不能早於開始日")
    _reject_unrelated_fields(rule)
    if rule.kind is RecurrenceKind.WEEKLY:
        weekdays = tuple(sorted(set(rule.weekdays)))
        if not weekdays:
            raise InvalidRecurrence("每週重複要選星期幾")
        if any(day < 0 or day > 6 for day in weekdays):
            raise InvalidRecurrence("星期只能是週一到週日")
        return replace(rule, weekdays=weekdays)
    day = _required(rule.day_of_month, "要填幾號")
    if day < 1 or day > 31:
        raise InvalidRecurrence("幾號要在 1 到 31 之間")
    if rule.kind is RecurrenceKind.EVERY_N_MONTHS:
        interval = _required(rule.interval_months, "要填每幾個月")
        if interval < MIN_INTERVAL_MONTHS or interval > MAX_INTERVAL_MONTHS:
            raise InvalidRecurrence(
                f"每幾個月要在 {MIN_INTERVAL_MONTHS} 到 {MAX_INTERVAL_MONTHS} 之間"
                "（每個月請選「每月」）"
            )
    if rule.kind is RecurrenceKind.YEARLY:
        month = _required(rule.month_of_year, "要填幾月")
        if month < 1 or month > 12:
            raise InvalidRecurrence("月份要在 1 到 12 之間")
        if day > calendar.monthrange(_LEAP_YEAR, month)[1]:
            raise InvalidRecurrence(f"{month} 月沒有 {day} 號")
    return rule


def occurrences_between(
    rule: Recurrence, start: date, end: date, *, limit: int = MAX_OCCURRENCES
) -> list[date]:
    """`[start, end]`（含兩端）內的發生日，由早到晚；最多 `limit` 個。"""
    low = max(start, rule.starts_on)
    high = end if rule.ends_on is None else min(end, rule.ends_on)
    if low > high:
        return []
    if rule.kind is RecurrenceKind.WEEKLY:
        return _weekly(set(rule.weekdays), low, high, limit)
    if rule.kind is RecurrenceKind.YEARLY:
        return _yearly(_required(rule.month_of_year, "要填幾月"), _day(rule), low, high, limit)
    interval = 1 if rule.kind is RecurrenceKind.MONTHLY else _required(rule.interval_months, "")
    return _monthly(rule.starts_on, interval, _day(rule), low, high, limit)


def is_occurrence(rule: Recurrence, day: date) -> bool:
    """這一天是不是規則的其中一次。"""
    return occurrences_between(rule, day, day) == [day]


def _reject_unrelated_fields(rule: Recurrence) -> None:
    kind = rule.kind
    if kind is not RecurrenceKind.WEEKLY and rule.weekdays:
        raise InvalidRecurrence("只有每週重複可以選星期")
    if kind is RecurrenceKind.WEEKLY and rule.day_of_month is not None:
        raise InvalidRecurrence("每週重複不用填幾號")
    if kind is not RecurrenceKind.EVERY_N_MONTHS and rule.interval_months is not None:
        raise InvalidRecurrence("只有「每幾個月」要填間隔")
    if kind is not RecurrenceKind.YEARLY and rule.month_of_year is not None:
        raise InvalidRecurrence("只有每年重複要填月份")


def _required(value: int | None, message: str) -> int:
    if value is None:
        raise InvalidRecurrence(message)
    return value


def _day(rule: Recurrence) -> int:
    return _required(rule.day_of_month, "要填幾號")


def _clamped(year: int, month: int, day: int) -> date:
    """29–31 號（或 2/29）遇到沒有這天的月份 → 該月最後一天。"""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _weekly(weekdays: set[int], low: date, high: date, limit: int) -> list[date]:
    found: list[date] = []
    day = low
    while day <= high and len(found) < limit:
        if day.weekday() in weekdays:
            found.append(day)
        day += timedelta(days=1)
    return found


def _monthly(
    anchor: date, interval: int, day: int, low: date, high: date, limit: int
) -> list[date]:
    anchor_index = anchor.year * 12 + anchor.month - 1
    low_index = low.year * 12 + low.month - 1
    # 從查詢起點所在的那個週期開始，不必從很久以前的開始月份一路數過來。
    steps = max(0, -(-(low_index - anchor_index) // interval))
    found: list[date] = []
    index = anchor_index + steps * interval
    while len(found) < limit:
        year, month_zero = divmod(index, 12)
        candidate = _clamped(year, month_zero + 1, day)
        if candidate > high:
            break
        if candidate >= low:
            found.append(candidate)
        index += interval
    return found


def _yearly(month: int, day: int, low: date, high: date, limit: int) -> list[date]:
    found: list[date] = []
    for year in range(low.year, high.year + 1):
        candidate = _clamped(year, month, day)
        if low <= candidate <= high:
            found.append(candidate)
            if len(found) >= limit:
                break
    return found
