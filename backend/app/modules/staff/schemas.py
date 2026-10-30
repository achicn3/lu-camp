"""staff I/O schema。"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.staff.models import STAFF_NAME_MAX


def _clean_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("姓名不能空白")
    return cleaned


class StaffMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    user_id: int | None
    is_active: bool


class StaffMemberCreate(BaseModel):
    name: str = Field(min_length=1, max_length=STAFF_NAME_MAX)
    user_id: int | None = None  # 對應的系統帳號（選填）

    _name = field_validator("name")(_clean_name)


class StaffMemberUpdate(BaseModel):
    """沒送的欄位不動；`user_id` 明確送 null＝取消帳號對應。"""

    name: str | None = Field(default=None, min_length=1, max_length=STAFF_NAME_MAX)
    user_id: int | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else _clean_name(value)

    @model_validator(mode="after")
    def _required_stay_set(self) -> Self:
        for name in ("name", "is_active"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} 不能清空")
        return self
