"""menu 的 Pydantic schema：餐飲菜單品項 CRUD（§11 合約）。

金額以字串傳輸（§11）、新台幣整數元（§6）：NTDAmount 序列化為字串。
更新採 PATCH 語意，以 `model_fields_set` 區分「未提供（不變）」與「明確設 null（清空 category）」。
"""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from app.modules.menu.models import MenuItem

NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]


class MenuItemCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    unit_price: Decimal = Field(gt=0)
    category: str | None = Field(default=None, max_length=50)
    sort_order: int = 0


class MenuItemUpdateRequest(BaseModel):
    """部分更新；未提供的欄位不變。category 可明確設為 null 以清空。"""

    name: str | None = Field(default=None, min_length=1, max_length=150)
    unit_price: Decimal | None = Field(default=None, gt=0)
    category: str | None = Field(default=None, max_length=50)
    sort_order: int | None = None
    is_available: bool | None = None


class MenuItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    name: str
    unit_price: NTDAmount
    category: str | None
    is_available: bool
    sort_order: int

    @classmethod
    def from_model(cls, item: MenuItem) -> "MenuItemRead":
        return cls.model_validate(item, from_attributes=True)
