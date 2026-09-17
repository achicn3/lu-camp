"""openingcheck I/O schema。"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class OpeningCheckItemRead(BaseModel):
    """一條自訂確認事項在「今天」的樣子。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    href: str | None
    done: bool = False


class OpeningCheckItemCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    # 「前往處理」要去哪（選填）：站內路徑，例如 /cash。
    href: str | None = Field(default=None, max_length=200)


class OpeningCheckTodayRead(BaseModel):
    """今天的檢查狀態。裝置狀態由前端直接問 hardware-agent，不在這裡。"""

    business_date: date
    cash_session_open: bool
    items: list[OpeningCheckItemRead]
    skipped_keys: list[str]
    # 後端管得到的部分是否都完成（開帳＋自訂項目）。裝置那幾項由前端自己併進去判斷，
    # 所以前端顯示的「全部完成」可能比這個嚴格。
    completed: bool


class OpeningCheckItemDoneRequest(BaseModel):
    done: bool


class OpeningCheckSkipRequest(BaseModel):
    """略過一個自動項目（`cash_session` 或 `device:<kind>:<id>`）。裁示：不必填原因。"""

    key: str = Field(min_length=1, max_length=100)
