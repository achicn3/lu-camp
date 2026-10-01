"""menu 的 Pydantic schema：餐飲菜單品項 CRUD（§11 合約）。

金額以字串傳輸（§11）、新台幣整數元（§6）：NTDAmount 序列化為字串。
更新採 PATCH 語意，以 `model_fields_set` 區分「未提供（不變）」與「明確設 null（清空 category）」。
"""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator

from app.core.money import ensure_ntd_fits_numeric_12, format_ntd
from app.modules.menu.models import MenuCategory, MenuOption
from app.modules.menu.service import (
    DailyStockEntry,
    MenuItemDetail,
    OptionGroupDetail,
    remaining_today,
    today,
)
from app.shared.enums import MenuStockAdjustReason, MenuStockTarget

# 一天的份數上限：防手滑多打幾個 0，正常餐點遠低於此。
DAILY_STOCK_MAX = 9999

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
    description: str | None = Field(default=None, max_length=500)
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
        if value < 0:
            raise ValueError("成本不可為負")  # 負成本會讓毛利報表憑空變大
        ensure_ntd_fits_numeric_12(value, field="成本")
        return value


class MenuItemUpdateRequest(BaseModel):
    """部分更新；未提供的欄位不變。category 可明確設為 null 以清空。"""

    name: str | None = Field(default=None, min_length=1, max_length=150)
    unit_price: Decimal | None = Field(default=None, gt=0)
    unit_cost: Decimal | None = Field(default=None, ge=0)  # 明確給 null＝清空成本
    category: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=500)  # 明確給 null＝清空
    sort_order: int | None = None
    is_available: bool | None = None
    # 每日限量開關（管理者）；份數本身由店員在開店檢查／POS 調整。
    daily_limited: bool | None = None

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
        if value < 0:
            raise ValueError("成本不可為負")  # 負成本會讓毛利報表憑空變大
        ensure_ntd_fits_numeric_12(value, field="成本")
        return value


def _valid_price_delta(value: Decimal) -> Decimal:
    if value != value.to_integral_value():
        raise ValueError("選項加價必須為整數元")
    if value < 0:
        raise ValueError("選項加價不可為負")
    ensure_ntd_fits_numeric_12(value, field="選項加價")
    return value


class MenuOptionRead(BaseModel):
    id: int
    group_id: int
    name: str
    price_delta: NTDAmount
    is_available: bool
    sort_order: int
    daily_limited: bool
    # 今天還能賣幾份；不限量＝null。
    remaining: int | None

    @classmethod
    def from_model(cls, option: MenuOption) -> "MenuOptionRead":
        return cls(
            id=option.id,
            group_id=option.group_id,
            name=option.name,
            price_delta=option.price_delta,
            is_available=option.is_available,
            sort_order=option.sort_order,
            daily_limited=option.daily_limited,
            remaining=remaining_today(option, today()),
        )


class MenuOptionGroupRead(BaseModel):
    id: int
    name: str
    min_select: int
    max_select: int
    sort_order: int
    options: list[MenuOptionRead]

    @classmethod
    def from_detail(cls, detail: OptionGroupDetail) -> "MenuOptionGroupRead":
        g = detail.group
        return cls(
            id=g.id,
            name=g.name,
            min_select=g.min_select,
            max_select=g.max_select,
            sort_order=g.sort_order,
            options=[MenuOptionRead.from_model(o) for o in detail.options],
        )


class MenuOptionInput(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    price_delta: Decimal = Decimal(0)

    @field_validator("price_delta")
    @classmethod
    def _valid_delta(cls, value: Decimal) -> Decimal:
        return _valid_price_delta(value)


class MenuOptionUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    price_delta: Decimal | None = None
    is_available: bool | None = None
    sort_order: int | None = None
    daily_limited: bool | None = None

    @field_validator("price_delta")
    @classmethod
    def _valid_delta(cls, value: Decimal | None) -> Decimal | None:
        return None if value is None else _valid_price_delta(value)


def _check_bounds(min_select: int, max_select: int) -> None:
    if min_select < 0 or max_select < 1 or min_select > max_select:
        raise ValueError("可選數量設定不正確：最少不可小於 0、最多至少 1，且最少不可大於最多")


class MenuOptionGroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    min_select: int
    max_select: int
    sort_order: int = 0
    options: list[MenuOptionInput] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _valid_bounds(self) -> "MenuOptionGroupCreateRequest":
        _check_bounds(self.min_select, self.max_select)
        return self


class MenuOptionGroupUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    min_select: int | None = None
    max_select: int | None = None
    sort_order: int | None = None


class MenuItemOptionGroupsRequest(BaseModel):
    """整批替換品項所掛的群組；順序即顯示順序。"""

    group_ids: list[int] = Field(max_length=20)


class MenuCategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    sort_order: int

    @classmethod
    def from_model(cls, category: MenuCategory) -> "MenuCategoryRead":
        return cls.model_validate(category, from_attributes=True)


class MenuCategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    sort_order: int = 0


class MenuCategoryUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    sort_order: int | None = None


class MenuItemRead(BaseModel):
    id: int
    store_id: int
    name: str
    unit_price: NTDAmount
    unit_cost: NTDAmountOpt
    # 分類名稱（相容舊欄位）＋ id；未分類兩者皆 null。
    category: str | None
    category_id: int | None
    description: str | None
    is_available: bool
    sort_order: int
    daily_limited: bool
    # 今天還能賣幾份；不限量＝null（0＝今天售完或還沒填）。
    remaining: int | None
    option_groups: list[MenuOptionGroupRead]

    @classmethod
    def from_detail(cls, detail: MenuItemDetail) -> "MenuItemRead":
        item = detail.item
        return cls(
            id=item.id,
            store_id=item.store_id,
            name=item.name,
            unit_price=item.unit_price,
            unit_cost=item.unit_cost,
            category=detail.category.name if detail.category is not None else None,
            category_id=detail.category.id if detail.category is not None else None,
            description=item.description,
            is_available=item.is_available,
            sort_order=item.sort_order,
            daily_limited=item.daily_limited,
            remaining=remaining_today(item, today()),
            option_groups=[MenuOptionGroupRead.from_detail(g) for g in detail.option_groups],
        )


class DailyStockEntryRead(BaseModel):
    """一個每日限量對象今天的狀態。"""

    kind: MenuStockTarget
    id: int
    label: str
    remaining: int
    # 今天填過沒有（填 0 也算）；開店檢查據此判斷是否完成。
    set_today: bool

    @classmethod
    def from_entry(cls, entry: DailyStockEntry) -> "DailyStockEntryRead":
        return cls(
            kind=entry.kind,
            id=entry.id,
            label=entry.label,
            remaining=entry.remaining,
            set_today=entry.set_today,
        )


class DailyStockSetRequest(BaseModel):
    """把今天的份數改成 qty；expected_remaining 是店員畫面上看到的數字（被搶先改過就拒絕）。"""

    qty: int = Field(ge=0, le=DAILY_STOCK_MAX)
    expected_remaining: int = Field(ge=0)


class DailyStockAdjustRequest(BaseModel):
    """今天的份數加減（剛做好 +4、報廢 −1）。"""

    delta: int = Field(ge=-DAILY_STOCK_MAX, le=DAILY_STOCK_MAX)
    # 加＝補貨（可省略）；減＝報廢或盤點校正（必填，報廢統計靠這個）。
    reason: MenuStockAdjustReason | None = None

    @model_validator(mode="after")
    def _reason_matches_sign(self) -> "DailyStockAdjustRequest":
        if self.delta == 0:
            raise ValueError("加減的份數不可為 0")
        if self.delta > 0 and self.reason not in (None, MenuStockAdjustReason.RESTOCK):
            raise ValueError("增加份數的原因只能是補貨")
        if self.delta < 0 and self.reason in (None, MenuStockAdjustReason.RESTOCK):
            raise ValueError("減少份數要選原因：報廢或盤點校正")
        return self
