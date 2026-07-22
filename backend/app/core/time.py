"""門市時間規則。

資料庫與 API 的瞬間維持 UTC；需要營業日期時，固定以台灣門市時區切界線。
"""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import AfterValidator

STORE_TIME_ZONE_NAME = "Asia/Taipei"
STORE_TIME_ZONE = ZoneInfo(STORE_TIME_ZONE_NAME)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("日期時間必須包含時區")
    return value.astimezone(UTC)


type AwareDateTime = Annotated[datetime, AfterValidator(_aware_utc)]


def utc_now() -> datetime:
    """回傳 timezone-aware UTC 現在時間；集中時間來源以利邊界測試。"""
    return datetime.now(UTC)


def store_date(value: datetime) -> date:
    """回傳某個瞬間所屬的台灣門市日期。"""
    return _aware_utc(value).astimezone(STORE_TIME_ZONE).date()


def store_datetime_iso(value: datetime) -> str:
    """供人閱讀的匯出欄位：以明確的台灣 ``+08:00`` 時區輸出 ISO 8601。"""
    return _aware_utc(value).astimezone(STORE_TIME_ZONE).isoformat()


def store_day_bounds(value: date) -> tuple[datetime, datetime]:
    """回傳台灣營業日對應的 UTC 半開區間 ``[start, end)``。"""
    local_start = datetime(value.year, value.month, value.day, tzinfo=STORE_TIME_ZONE)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def store_bucket_bounds(granularity: str, value: datetime) -> tuple[datetime, datetime]:
    """回傳某瞬間所屬台灣日／週／月／季的 UTC 半開區間。"""
    local = _aware_utc(value).astimezone(STORE_TIME_ZONE)
    local_day = datetime(local.year, local.month, local.day, tzinfo=STORE_TIME_ZONE)
    if granularity == "day":
        start = local_day
        end = start + timedelta(days=1)
    elif granularity == "week":
        start = local_day - timedelta(days=local_day.weekday())
        end = start + timedelta(days=7)
    elif granularity == "month":
        start = datetime(local.year, local.month, 1, tzinfo=STORE_TIME_ZONE)
        end = (
            datetime(local.year + 1, 1, 1, tzinfo=STORE_TIME_ZONE)
            if local.month == 12
            else datetime(local.year, local.month + 1, 1, tzinfo=STORE_TIME_ZONE)
        )
    elif granularity == "quarter":
        quarter_month = ((local.month - 1) // 3) * 3 + 1
        start = datetime(local.year, quarter_month, 1, tzinfo=STORE_TIME_ZONE)
        end = (
            datetime(local.year + 1, 1, 1, tzinfo=STORE_TIME_ZONE)
            if quarter_month == 10
            else datetime(local.year, quarter_month + 3, 1, tzinfo=STORE_TIME_ZONE)
        )
    else:
        raise ValueError(f"不支援的門市時間分桶：{granularity}")
    return start.astimezone(UTC), end.astimezone(UTC)
