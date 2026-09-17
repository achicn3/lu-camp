"""menu 的 Pydantic schema：餐飲菜單品項 CRUD（§11 合約）。

金額以字串傳輸（§11）、新台幣整數元（§6）：NTDAmount 序列化為字串。
更新採 PATCH 語意，以 `model_fields_set` 區分「未提供（不變）」與「明確設 null（清空 category）」。
"""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.core.money import ensure_ntd_fits_numeric_12, format_ntd
from app.modules.menu.models import MenuItem

NTDAmount = Annotated[Decimal, PlainSerializer(format_ntd, return_type=str)]
NTDAmountOpt = Annotated[
    Decimal | None,
    PlainSerializer(lambda d: None if d is None else format_ntd(d), return_type=str | None),
]


class MenuItemCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    unit_price: Decimal = Field(gt=0)
    # 成本可不填（不知道就誠實留空，不要填 0——那會讓報表以為毛利 100%）。
    unit_cost: Decimal | None = Field(default=None, ge=0)
    category: str | None = Field(default=None, max_length=50)
    sort_order: int = 0

    @field_validator("unit_price")
    @classmethod
    def _valid_unit_price(cls, value: Decimal) -> Decimal:
        if value != value.to_integral_value():
            raise ValueError("售價必須為整數元")
        ensure_ntd_fits_numeric_12(value, field="售價")
        return value

    @field_validator("unit_cost")
    @classmethod
    def _valid_unit_cost(cls, value: Decimal | None) -> Decimal | None:
        """成本與金額全系統慣例一致：整數元（§6）。可為 None＝不知道。"""
        if value is None:
            return None
        if value != value.to_integral_value():
            raise ValueError("成本必須為整數元")
        ensure_ntd_fits_numeric_12(value, field="成本")
        return value


class MenuItemUpdateRequest(BaseModel):
    """部分更新；未提供的欄位不變。category 可明確設為 null 以清空。"""

    name: str | None = Field(default=None, min_length=1, max_length=150)
    unit_price: Decimal | None = Field(default=None, gt=0)
    unit_cost: Decimal | None = Field(default=None, ge=0)  # 明確給 null＝清空成本
    category: str | None = Field(default=None, max_length=50)
    sort_order: int | None = None
    is_available: bool | None = None

    @field_validator("unit_price")
    @classmethod
    def _valid_unit_price(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        if value != value.to_integral_value():
            raise ValueError("售價必須為整數元")
        ensure_ntd_fits_numeric_12(value, field="售價")
        return value

    @field_validator("unit_cost")
    @classmethod
    def _valid_unit_cost(cls, value: Decimal | None) -> Decimal | None:
        """成本與金額全系統慣例一致：整數元（§6）。可為 None＝不知道。"""
        if value is None:
            return None
        if value != value.to_integral_value():
            raise ValueError("成本必須為整數元")
        ensure_ntd_fits_numeric_12(value, field="成本")
        return value


class MenuItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    name: str
    unit_price: NTDAmount
    unit_cost: NTDAmountOpt
    category: str | None
    is_available: bool
    sort_order: int

    @classmethod
    def from_model(cls, item: MenuItem) -> "MenuItemRead":
        return cls.model_validate(item, from_attributes=True)
