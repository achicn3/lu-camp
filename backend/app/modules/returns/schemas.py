"""returns API schemas。"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from app.modules.returns.models import CustomerReturn

NTDAmount = Annotated[Decimal, PlainSerializer(lambda d: str(d), return_type=str)]


class ReturnLineRequest(BaseModel):
    sale_line_id: int
    qty: int = Field(gt=0)


class ReturnCreateRequest(BaseModel):
    sale_id: int
    reason: str = Field(min_length=1, max_length=500)
    lines: list[ReturnLineRequest] = Field(min_length=1)


class ReturnLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sale_line_id: int
    qty: int
    refund_amount: NTDAmount


class ReturnRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    store_id: int
    sale_id: int
    refund_amount: NTDAmount
    reason: str
    clerk_user_id: int
    created_at: datetime
    lines: list[ReturnLineRead]

    @classmethod
    def from_model(cls, customer_return: CustomerReturn) -> "ReturnRead":
        return cls.model_validate(customer_return)
