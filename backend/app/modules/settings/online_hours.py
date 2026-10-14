"""Weekly online ordering hours, in Asia/Taipei; Monday first, null means closed."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

ClockTime = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]


class OnlineHoursDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opens: ClockTime
    closes: ClockTime

    @model_validator(mode="after")
    def different_endpoints(self) -> Self:
        if self.opens == self.closes:
            raise ValueError("開始與結束時間不可相同；跨午夜請把結束時間設為隔日時間")
        return self


class OnlineHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool = False
    days: list[OnlineHoursDay | None] = Field(
        default_factory=lambda: list[OnlineHoursDay | None]([None] * 7), min_length=7, max_length=7
    )
