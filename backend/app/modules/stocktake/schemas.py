"""stocktake 讀寫 schema：盤點單查詢、確認輸入。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.stocktake.models import Stocktake
from app.shared.enums import StocktakeStatus


class StocktakeLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    catalog_product_id: int
    system_qty: int
    counted_qty: int | None
    variance: int | None  # 實點 − 快照；未點為 null（model 屬性）


class StocktakeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    status: StocktakeStatus
    created_by: int
    created_at: datetime
    confirmed_by: int | None
    confirmed_at: datetime | None
    lines: list[StocktakeLineRead]

    @classmethod
    def from_model(cls, stocktake: Stocktake) -> "StocktakeRead":
        return cls.model_validate(stocktake)


class StocktakeCountInput(BaseModel):
    """單筆實點：商品 + 實點數（不可為負）。"""

    catalog_product_id: int
    counted_qty: int = Field(ge=0)


class StocktakeConfirmRequest(BaseModel):
    """確認盤點輸入：各商品實點數（未列入者不調整）。"""

    counts: list[StocktakeCountInput]
