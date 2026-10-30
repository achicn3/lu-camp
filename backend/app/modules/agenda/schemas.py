"""agenda I/O schema（docs/99 §4、§8、§9）。

更新用的 schema 一律「沒送的欄位不動」：服務層看 `model_fields_set`，
所以可以明確送 null 清掉選填欄位；
必填欄位（標題、只限管理者、提前天數…）送 null 直接 422。
"""

from datetime import date, datetime, time, timedelta
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.shared.enums import (
    AgendaEventKind,
    AgendaItemKind,
    AgendaTaskStatus,
    CampaignStatus,
    RecurrenceKind,
)

TITLE_MAX = 100
NOTE_MAX = 1000
LINK_MAX = 200
DEFAULT_LEAD_DAYS = 3  # 到期前 3 天起出現在「快到期」（docs/99 §1 預設）
MAX_LEAD_DAYS = 365
MAX_EVENT_DAYS = 366  # 一個事件最長跨幾天（例：整修長假）
MAX_FEED_DAYS = 93  # 行事曆一次最多查幾天（月曆六週＝42 天，留餘裕）


def _clean_title(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("標題不能空白")
    return cleaned


def _clean_note(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _internal_path(value: str | None) -> str | None:
    """只收站內路徑（同開店前檢查的「前往處理」）。

    `//evil.com` 與 `/\\evil.com` 都以 `/` 開頭卻會導到站外（瀏覽器把 `\\` 當 `/`），一併擋掉。
    """
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned == "":
        return None
    if not cleaned.startswith("/") or cleaned[1:2] in {"/", "\\"}:
        raise ValueError("連結只能是站內路徑（例如 /purchasing/12）")
    return cleaned


def _reject_nulls(model: BaseModel, *names: str) -> None:
    for name in names:
        if name in model.model_fields_set and getattr(model, name) is None:
            raise ValueError(f"{name} 不能清空")


class RecurrenceFields(BaseModel):
    """重複規則（不含開始日：待辦用 `starts_on`、事件用自己的開始日當起點）。"""

    kind: RecurrenceKind
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    day_of_month: int | None = None
    interval_months: int | None = None
    month_of_year: int | None = None
    until: date | None = None  # 最後一次不晚於這天（含）；空白＝一直重複


class _TaskFields(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    note: str | None = Field(default=None, max_length=NOTE_MAX)
    assignee_staff_id: int | None = None
    manager_only: bool = False
    link_href: str | None = Field(default=None, max_length=LINK_MAX)
    lead_days: int = Field(default=DEFAULT_LEAD_DAYS, ge=0, le=MAX_LEAD_DAYS)

    _title = field_validator("title")(_clean_title)
    _note = field_validator("note")(_clean_note)
    _link = field_validator("link_href")(_internal_path)


class AgendaTaskCreate(_TaskFields):
    """單次待辦。沒有日期的放「沒有日期」清單；有時間就一定要有日期。"""

    due_on: date | None = None
    due_time: time | None = None

    @model_validator(mode="after")
    def _time_needs_date(self) -> Self:
        if self.due_time is not None and self.due_on is None:
            raise ValueError("有時間就要選日期")
        return self


class AgendaTaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    note: str | None = Field(default=None, max_length=NOTE_MAX)
    due_on: date | None = None
    due_time: time | None = None
    assignee_staff_id: int | None = None
    manager_only: bool | None = None
    link_href: str | None = Field(default=None, max_length=LINK_MAX)
    lead_days: int | None = Field(default=None, ge=0, le=MAX_LEAD_DAYS)

    _note = field_validator("note")(_clean_note)
    _link = field_validator("link_href")(_internal_path)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        return None if value is None else _clean_title(value)

    @model_validator(mode="after")
    def _required_stay_set(self) -> Self:
        _reject_nulls(self, "title", "manager_only", "lead_days")
        return self


class AgendaTaskStatusRequest(BaseModel):
    status: AgendaTaskStatus


class AgendaTaskRead(BaseModel):
    """一筆待辦：單次的，或重複待辦已經被打勾、略過、修改過的那一次。"""

    id: int
    title: str
    note: str | None
    due_on: date | None
    due_time: time | None
    assignee_staff_id: int | None
    assignee_name: str | None
    manager_only: bool
    link_href: str | None
    lead_days: int
    series_id: int | None
    occurrence_on: date | None
    status: AgendaTaskStatus
    resolved_at: datetime | None
    resolved_by_name: str | None
    created_at: datetime


class AgendaSeriesCreate(_TaskFields):
    """重複待辦：從 `starts_on` 起依規則出現，每一次都是獨立的一筆。"""

    due_time: time | None = None
    starts_on: date
    recurrence: RecurrenceFields


class AgendaSeriesUpdate(BaseModel):
    """改規則或開始日：之前沒做的那幾次照舊留著提醒，新規則從今天起算（ADR-034 決定 6）。"""

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    note: str | None = Field(default=None, max_length=NOTE_MAX)
    due_time: time | None = None
    assignee_staff_id: int | None = None
    manager_only: bool | None = None
    link_href: str | None = Field(default=None, max_length=LINK_MAX)
    lead_days: int | None = Field(default=None, ge=0, le=MAX_LEAD_DAYS)
    starts_on: date | None = None
    recurrence: RecurrenceFields | None = None

    _note = field_validator("note")(_clean_note)
    _link = field_validator("link_href")(_internal_path)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        return None if value is None else _clean_title(value)

    @model_validator(mode="after")
    def _required_stay_set(self) -> Self:
        _reject_nulls(self, "title", "manager_only", "lead_days", "starts_on", "recurrence")
        return self


class AgendaSeriesRead(BaseModel):
    id: int
    title: str
    note: str | None
    due_time: time | None
    assignee_staff_id: int | None
    assignee_name: str | None
    manager_only: bool
    link_href: str | None
    lead_days: int
    starts_on: date
    recurrence: RecurrenceFields
    next_on: date | None  # 今天起下一次（已結束的規則為 null）


class AgendaOccurrenceUpdate(BaseModel):
    """只改重複待辦的這一次（例：這週改週二叫牛奶）。"""

    due_on: date | None = None
    due_time: time | None = None
    note: str | None = Field(default=None, max_length=NOTE_MAX)
    assignee_staff_id: int | None = None

    _note = field_validator("note")(_clean_note)

    @model_validator(mode="after")
    def _date_stays_set(self) -> Self:
        _reject_nulls(self, "due_on")
        return self


class AgendaSkipOverdueRead(BaseModel):
    skipped: int


class _EventFields(BaseModel):
    @model_validator(mode="after")
    def _window(self) -> Self:
        starts_on: date | None = getattr(self, "starts_on", None)
        ends_on: date | None = getattr(self, "ends_on", None)
        if starts_on is not None and ends_on is not None:
            if ends_on < starts_on:
                raise ValueError("結束日不能早於開始日")
            if ends_on - starts_on > timedelta(days=MAX_EVENT_DAYS):
                raise ValueError(f"一個事件最多 {MAX_EVENT_DAYS} 天")
        return self


class AgendaEventCreate(_EventFields):
    kind: AgendaEventKind
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    note: str | None = Field(default=None, max_length=NOTE_MAX)
    starts_on: date
    ends_on: date | None = None  # 空白＝當天
    start_time: time | None = None
    end_time: time | None = None
    manager_only: bool = False
    recurrence: RecurrenceFields | None = None

    _title = field_validator("title")(_clean_title)
    _note = field_validator("note")(_clean_note)


class AgendaEventUpdate(_EventFields):
    kind: AgendaEventKind | None = None
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    note: str | None = Field(default=None, max_length=NOTE_MAX)
    starts_on: date | None = None
    ends_on: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    manager_only: bool | None = None
    recurrence: RecurrenceFields | None = None  # 明確送 null＝改成不重複

    _note = field_validator("note")(_clean_note)

    @field_validator("title")
    @classmethod
    def _title(cls, value: str | None) -> str | None:
        return None if value is None else _clean_title(value)

    @model_validator(mode="after")
    def _required_stay_set(self) -> Self:
        _reject_nulls(self, "kind", "title", "starts_on", "ends_on", "manager_only")
        return self


class AgendaEventOccurrenceRequest(BaseModel):
    """取消（或恢復）重複事件的某一次，例：這個週一照常營業。"""

    on: date
    cancelled: bool = True


class AgendaEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: AgendaEventKind
    title: str
    note: str | None
    starts_on: date
    ends_on: date
    start_time: time | None
    end_time: time | None
    manager_only: bool
    recurrence: RecurrenceFields | None
    cancelled_dates: list[date]


class AgendaItemRead(BaseModel):
    """行事曆上的一列。

    `key` 穩定，可當畫面的 key；操作時看 task_id、series_id＋occurrence_on 或 event_id。
    """

    key: str
    kind: AgendaItemKind
    # 欄位不叫 date／time：會在類別裡遮住同名型別（Pydantic 依序解析標註）。
    day: date
    end_day: date | None = None  # 跨天事件的最後一天
    start_time: time | None = None
    end_time: time | None = None
    title: str
    note: str | None = None
    status: AgendaTaskStatus | None = None
    overdue: bool = False
    manager_only: bool = False
    assignee_staff_id: int | None = None
    assignee_name: str | None = None
    lead_days: int | None = None
    task_id: int | None = None
    series_id: int | None = None
    occurrence_on: date | None = None
    event_id: int | None = None
    event_kind: AgendaEventKind | None = None
    campaign_id: int | None = None
    campaign_status: CampaignStatus | None = None
    href: str | None = None


class AgendaFeedRead(BaseModel):
    start: date
    end: date
    items: list[AgendaItemRead]


class AgendaTodayRead(BaseModel):
    """首頁與開店前檢查的「今天」（docs/99 §8）。只含這個人看得到的。"""

    today: date
    due_today: list[AgendaItemRead]  # 今天到期（含已完成的，畫面可劃掉）
    overdue: list[AgendaItemRead]  # 過了到期日還沒做
    upcoming: list[AgendaItemRead]  # 快到期：在各自的提前天數內
    events: list[AgendaItemRead]  # 今天的公休、市集等
    campaigns: list[AgendaItemRead]  # 今天開始或結束的門市活動
    consignment_pending_count: int  # 寄售待撥款筆數
    badge_count: int  # 導覽列數字：今天到期（未完成）＋已過期
